"""Injection screen — normalise, unmask look-alikes, scan in English and Arabic (TRD 6 layers 1 and 4).

Runs on all ingested text (CP0, intake, seeding) and at chat entry, under
250 ms per message (it is regular expressions over short text):

  1. Normalise: NFKC, strip zero-width and bidi control characters
     (right-to-left override), Arabic-Indic digits → 0-9.
  2. Unmask look-alike characters: Cyrillic or Greek letters inside a word
     that also has Latin letters ("іgnore" with a Cyrillic і) are folded to
     Latin before scanning.
  3. Scan the English and Arabic instruction patterns.

A pattern hit, hidden characters or look-alike letters mark the text for
quarantine. Instruction-like words alone ("prompt", "instructions") only
flag it for the fast-tier classifier, which runs off the live chat path.

The screen never changes what is stored as evidence: the text stays data.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Cyrillic and Greek letters that look like Latin ones.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ɡ": "g",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "τ": "t", "υ": "u",
    "Α": "A", "Β": "B", "Ε": "E", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Χ": "X", "Υ": "Y", "Ζ": "Z",
})
_LOOKALIKE_CHARS = re.compile("[" + "".join(chr(c) for c in _CONFUSABLES) + "]")
_WORD = re.compile(r"\w+", re.UNICODE)
_LATIN = re.compile(r"[A-Za-z]")
_INSTRUCTION_WORDS = re.compile(
    r"\b(?:instructions?|system prompt|prompt|assistant|jailbreak|override|developer mode)\b|تعليمات|الأوامر|تجاهل",
    re.I,
)


@dataclass
class ScreenResult:
    original: str
    text: str  # normalised, look-alikes folded: what routing and scanning see
    source: str
    hits: list[dict] = field(default_factory=list)  # [{pattern_id, matched_text, language}]
    flags: list[str] = field(default_factory=list)  # hidden_characters | look_alike | instruction_words
    elapsed_ms: float = 0.0

    @property
    def quarantine(self) -> bool:
        return bool(self.hits) or "hidden_characters" in self.flags or "look_alike" in self.flags

    @property
    def needs_classifier(self) -> bool:
        """Flagged text gets the fast-tier classifier (off the live chat path)."""
        return self.quarantine or "instruction_words" in self.flags

    @property
    def reason(self) -> str | None:
        parts = []
        if self.hits:
            parts.append("instruction pattern " + ", ".join(
                f"{h['pattern_id']} ({h['matched_text']!r})" for h in self.hits))
        if "hidden_characters" in self.flags:
            parts.append("hidden characters stripped (zero-width or right-to-left override)")
        if "look_alike" in self.flags:
            parts.append("look-alike letters mixed into Latin words")
        if not parts and "instruction_words" in self.flags:
            parts.append("instruction-like wording")
        return "; ".join(parts) or None


def fold_lookalikes(text: str) -> tuple[str, bool]:
    """Fold Cyrillic/Greek look-alikes to Latin inside words that also contain Latin letters."""
    changed = False

    def fold(match: re.Match) -> str:
        nonlocal changed
        word = match.group(0)
        if _LATIN.search(word) and _LOOKALIKE_CHARS.search(word):
            changed = True
            return word.translate(_CONFUSABLES)
        return word

    return _WORD.sub(fold, text), changed


def screen_text(text: str, *, source: str) -> ScreenResult:
    # Imported here: the CP0 modules live in the orchestration package, which itself uses the screen.
    from app.orchestration.injection_scan import scan
    from app.orchestration.text_normaliser import normalise

    start = time.perf_counter()
    norm = normalise(text or "")
    folded, look_alike = fold_lookalikes(norm.normalised)
    result = ScreenResult(original=text or "", text=folded, source=source)
    result.hits = [{"pattern_id": h.pattern_id, "matched_text": h.matched_text, "language": h.language}
                   for h in scan(folded).hits]
    if norm.had_control_chars:
        result.flags.append("hidden_characters")
    if look_alike:
        result.flags.append("look_alike")
    if _INSTRUCTION_WORDS.search(folded):
        result.flags.append("instruction_words")
    result.elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return result
