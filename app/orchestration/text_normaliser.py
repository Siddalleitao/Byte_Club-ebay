"""CP0 gate — text normaliser.

Applied to every ingested string before anything else sees it:
- Unicode NFKC normalisation
- Strip zero-width and bidirectional control characters (TRD 6 defence layer 1)
- Convert Arabic-Indic digits ٠-٩ to ASCII 0-9 (E10), keeping raw text as evidence
- Cap field length
- Retry garbled Arabic as UTF-8 then Windows-1256 (E09)

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ── Edge-case IDs this module addresses ──
# E09: Arabic text garbled by encoding — retry as UTF-8, then Windows-1256
# E10: Arabic-Indic digits (٢٠١٨) → convert to 0-9, keep raw text as evidence

# Arabic-Indic digits ٠١٢٣٤٥٦٧٨٩ (U+0660–U+0669) → ASCII 0-9
_ARABIC_INDIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Extended Arabic-Indic digits ۰۱۲۳۴۵۶۷۸۹ (U+06F0–U+06F9) → ASCII 0-9
_EXT_ARABIC_INDIC_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# Zero-width and bidirectional control characters to strip
# U+200B zero-width space, U+200C ZWNJ, U+200D ZWJ, U+200E LRM, U+200F RLM,
# U+202A LRE, U+202B RLE, U+202C PDF, U+202D LRO, U+202E RLO,
# U+2066 LRI, U+2067 RLI, U+2068 FSI, U+2069 PDI
_CONTROL_CHARS = set(
    "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\ufeff"
)

# Default max field length (characters)
DEFAULT_MAX_LENGTH = 2000


@dataclass
class NormalisedText:
    """Result of normalising a text string.

    raw_text: the original text, kept as evidence (per E10: "keep raw text as evidence")
    normalised: the cleaned text for downstream processing
    had_control_chars: whether zero-width / bidi controls were stripped
    had_arabic_indic: whether Arabic-Indic digits were converted
    encoding_retry: 'utf-8' if original, 'windows-1256' if retried (E09), or 'raw'
    truncated: whether the text was capped to max_length
    """

    raw_text: str
    normalised: str
    had_control_chars: bool
    had_arabic_indic: bool
    encoding_retry: str
    truncated: bool


def _strip_control_chars(text: str) -> tuple[str, bool]:
    """Remove zero-width and bidi control characters. Returns (cleaned, had_any)."""
    result = []
    had = False
    for ch in text:
        if ch in _CONTROL_CHARS:
            had = True
        else:
            result.append(ch)
    return "".join(result), had


def clean_keep_digits(text: str) -> str:
    """NFKC and hidden-character stripping only: Arabic-Indic digits are kept as typed.

    For text that is stored as evidence (E10: convert for use, keep the raw text).
    """
    cleaned, _ = _strip_control_chars(unicodedata.normalize("NFKC", text))
    return cleaned


def _convert_arabic_indic(text: str) -> tuple[str, bool]:
    """Convert Arabic-Indic digits to ASCII. Returns (converted, had_any)."""
    converted = text.translate(_ARABIC_INDIC_MAP).translate(_EXT_ARABIC_INDIC_MAP)
    had = converted != text
    return converted, had


def _cap_length(text: str, max_length: int) -> tuple[str, bool]:
    """Cap text to max_length characters. Returns (capped, was_truncated)."""
    if len(text) <= max_length:
        return text, False
    return text[:max_length], True


def normalise(
    text: str,
    *,
    max_length: int = DEFAULT_MAX_LENGTH,
    raw_bytes: bytes | None = None,
) -> NormalisedText:
    """Normalise an ingested string.

    Args:
        text: The string to normalise.
        max_length: Maximum character length for the field.
        raw_bytes: Original bytes if available, for encoding retry (E09).
                   If text contains mojibake (garbled Arabic), we retry
                   decoding raw_bytes as UTF-8 then Windows-1256.

    Returns:
        NormalisedText with raw_text preserved as evidence.
    """
    raw_text = text  # keep original as evidence per E10

    # E09: Retry garbled Arabic as UTF-8 then Windows-1256
    encoding_retry = "raw"
    working_text = text

    if raw_bytes is not None:
        # Check if the text looks like garbled Arabic (mojibake)
        # Common sign: replacement chars or non-Arabic garbage where Arabic should be
        if "\ufffd" in text or _looks_mojibake(text):
            retried = _retry_encoding(raw_bytes)
            if retried is not None:
                working_text = retried
                encoding_retry = "windows-1256" if retried != text else "utf-8"
                raw_text = working_text  # update raw to the correctly decoded version

    # Step 1: Unicode NFKC normalisation
    working_text = unicodedata.normalize("NFKC", working_text)

    # Step 2: Strip zero-width and bidi control characters (TRD 6 layer 1)
    working_text, had_control = _strip_control_chars(working_text)

    # Step 3: Convert Arabic-Indic digits to ASCII (E10)
    working_text, had_indic = _convert_arabic_indic(working_text)

    # Step 4: Cap field length
    working_text, truncated = _cap_length(working_text, max_length)

    return NormalisedText(
        raw_text=raw_text,
        normalised=working_text,
        had_control_chars=had_control,
        had_arabic_indic=had_indic,
        encoding_retry=encoding_retry,
        truncated=truncated,
    )


def _looks_mojibake(text: str) -> bool:
    """Heuristic: detect text that looks like garbled Arabic encoding."""
    # If the text has Arabic codepoints mixed with Latin-1 supplement chars
    # (like Ã, ©, ¦), it's likely mojibake from UTF-8 misread as Windows-1252
    has_latin1_garbage = bool(re.search(r"[Ã©Â¦¼¿]", text))
    has_arabic = any("\u0600" <= ch <= "\u06FF" for ch in text)
    # Also check for sequences of replacement or control chars
    has_replacement = "\ufffd" in text
    return has_latin1_garbage or (has_replacement and not has_arabic)


def _retry_encoding(raw_bytes: bytes) -> str | None:
    """Retry decoding bytes as UTF-8 then Windows-1256 (E09).

    Returns the correctly decoded string, or None if both fail.
    """
    # Try UTF-8 first (strict mode)
    try:
        return raw_bytes.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        pass

    # Try Windows-1256 (Arabic codepage)
    try:
        return raw_bytes.decode("windows-1256")
    except (UnicodeDecodeError, AttributeError):
        pass

    return None


def detect_language(text: str) -> str:
    """Detect the dominant language of a text string.

    Returns 'ar' for Arabic, 'en' for English, 'mixed' for both.
    """
    has_arabic = any("\u0600" <= ch <= "\u06FF" for ch in text)
    # Look for English words: 2+ consecutive ASCII letters
    has_english = bool(re.search(r"[A-Za-z]{2,}", text))

    if has_arabic and has_english:
        return "mixed"
    elif has_arabic:
        return "ar"
    elif has_english:
        return "en"
    return "unknown"
