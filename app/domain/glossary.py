"""Bilingual glossary and controlled vocabularies (TRD 4).

Loads data/reference/glossary.yaml and normalises English or Arabic free
text into fixed values: position, condition, defect, test status, features,
part type and field names. Also normalises Arabic-Indic digits to 0-9.

Rules enforced here:
  - Left and right are absolute, as seen from the driver's seat.
  - "Driver side" / "passenger side" are recognised as ambiguous and are
    never returned as a value (E22). The caller must ask.
  - Matching is deterministic (whole phrase, longest first). Nothing is
    guessed: text with no vocabulary phrase normalises to no value.

Deterministic normalisation, so it lives in the domain tier (domain services
and the semantic layer both use it).

Per Rule (f): the text being normalised is data. It only ever selects a
value from a fixed list; it cannot add values or change behaviour.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

GLOSSARY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reference" / "glossary.yaml"

_ARABIC_DIACRITICS = re.compile(r"[ً-ْٰـ]")  # harakat, dagger alef, tatweel
_ALEF_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_NON_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class TermMatch:
    """Result of normalising text against one vocabulary."""

    domain: str
    value: str | None  # the single normalised value, or None
    values: tuple[str, ...] = ()  # every distinct value found, in order
    ambiguous: bool = False  # an ambiguous phrase was found and no absolute value
    forbidden: bool = False  # a forbidden phrase was found (e.g. "brand new")
    matched: tuple[str, ...] = ()  # the phrases that matched, folded


@dataclass
class Vocabulary:
    domain: str
    values: dict[str, list[str]]  # value -> folded synonyms (en + ar)
    ambiguous: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # value -> extra keys
    note: str | None = None
    raw: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # value -> {"en", "ar"}

    @property
    def allowed(self) -> tuple[str, ...]:
        return tuple(self.values)


class Glossary:
    """Bilingual glossary + controlled vocabularies, loaded from YAML."""

    def __init__(self, data: dict[str, Any]):
        self.version: str = str(data.get("version", ""))
        self.terms: list[dict[str, Any]] = data.get("terms", [])
        digits = data.get("digits", {})
        mapping: dict[str, str] = {}
        for digit_string in digits.values():
            for ascii_digit, ch in enumerate(digit_string):
                mapping[ch] = str(ascii_digit)
        self._digit_table = str.maketrans(mapping)
        self.vocabularies: dict[str, Vocabulary] = {
            domain: self._load_vocabulary(domain, spec)
            for domain, spec in data.get("vocabularies", {}).items()
        }

    # ── Loading ──

    @classmethod
    def load(cls, path: Path | str = GLOSSARY_PATH) -> "Glossary":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def _load_vocabulary(self, domain: str, spec: dict[str, Any]) -> Vocabulary:
        values: dict[str, list[str]] = {}
        meta: dict[str, dict[str, Any]] = {}
        raw: dict[str, dict[str, list[str]]] = {}
        for value, entry in spec.get("values", {}).items():
            phrases = [*entry.get("en", []), *entry.get("ar", [])]
            values[value] = [self.fold(p) for p in phrases if self.fold(p)]
            raw[value] = {"en": entry.get("en", []), "ar": entry.get("ar", [])}
            meta[value] = {k: v for k, v in entry.items() if k not in ("en", "ar")}
        ambiguous = spec.get("ambiguous", {})
        forbidden = spec.get("forbidden", {})
        return Vocabulary(
            domain=domain,
            values=values,
            ambiguous=[self.fold(p) for p in [*ambiguous.get("en", []), *ambiguous.get("ar", [])]],
            forbidden=[self.fold(p) for p in [*forbidden.get("en", []), *forbidden.get("ar", [])]],
            meta=meta,
            note=spec.get("note"),
            raw=raw,
        )

    # ── Text folding ──

    def normalise_digits(self, text: str) -> str:
        """Convert Arabic-Indic and extended Arabic-Indic digits to ASCII 0-9 (E10)."""
        return text.translate(self._digit_table)

    def fold(self, text: str) -> str:
        """Fold text for matching: NFKC, digits, Arabic letter forms, case, punctuation."""
        text = unicodedata.normalize("NFKC", text)
        text = self.normalise_digits(text)
        text = _ARABIC_DIACRITICS.sub("", text).translate(_ALEF_FORMS)
        text = text.replace("'", "").replace("’", "")
        text = _NON_WORD.sub(" ", text.lower())
        return _SPACES.sub(" ", text).strip()

    # ── Normalisation ──

    def normalise(self, domain: str, text: str | None) -> TermMatch:
        """Normalise free text into one controlled value of `domain`.

        `value` is set only when exactly one distinct value is found. Two
        different values (e.g. "left ... right") give no value: never guess.
        """
        vocab = self.vocabularies[domain]
        if not text:
            return TermMatch(domain=domain, value=None)
        work = f" {self.fold(text)} "

        found: list[tuple[int, str, str]] = []  # (position, value, phrase)
        flagged: dict[str, list[str]] = {"ambiguous": [], "forbidden": []}
        candidates = [(p, v) for v, phrases in vocab.values.items() for p in phrases]
        candidates += [(p, "\0ambiguous") for p in vocab.ambiguous]
        candidates += [(p, "\0forbidden") for p in vocab.forbidden]
        # Longest phrase first, so "not working" wins over "working" and
        # "broken glass" over "broken". Matched spans are blanked out.
        for phrase, value in sorted(candidates, key=lambda c: len(c[0]), reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
            for m in list(pattern.finditer(work)):
                if value.startswith("\0"):
                    flagged[value[1:]].append(phrase)
                else:
                    found.append((m.start(), value, phrase))
            work = pattern.sub(lambda m: " " * len(m.group(0)), work)

        found.sort()
        distinct = tuple(dict.fromkeys(v for _, v, _ in found))
        return TermMatch(
            domain=domain,
            value=distinct[0] if len(distinct) == 1 else None,
            values=distinct,
            ambiguous=bool(flagged["ambiguous"]) and not distinct,
            forbidden=bool(flagged["forbidden"]),
            matched=tuple(p for _, _, p in found) + tuple(flagged["ambiguous"] + flagged["forbidden"]),
        )

    def is_allowed(self, domain: str, value: str) -> bool:
        return value in self.vocabularies[domain].values

    def allowed_values(self, domain: str) -> tuple[str, ...]:
        return self.vocabularies[domain].allowed

    def field_name(self, term: str) -> str:
        """Resolve a business term ("weight", "الوزن") to an attribute field name.

        Returns the term unchanged when it is already a field name or unknown.
        """
        if term in self.vocabularies["field"].values:
            return term
        match = self.normalise("field", term)
        return match.value or term

    def uk_search_terms(self, part_type: str) -> list[str]:
        """UK buyer search terms for a part type (e.g. wing mirror, door mirror)."""
        return list(self.vocabularies["part_type"].meta.get(part_type, {}).get("uk_search_terms", []))

    def is_excluded_part_type(self, part_type: str) -> bool:
        return bool(self.vocabularies["part_type"].meta.get(part_type, {}).get("excluded"))

    # ── Prompt rendering ──

    def render_for_prompt(self) -> str:
        """Compact, deterministic rendering for the cached prompt prefix."""
        lines = [f"GLOSSARY AND CONTROLLED VOCABULARIES (v{self.version})"]
        lines.append("Arabic-Indic digits (٠-٩, ۰-۹) are read as 0-9.")
        for domain, vocab in self.vocabularies.items():
            lines.append(f"[{domain}] allowed values: {', '.join(vocab.allowed)}")
            if vocab.note:
                lines.append(f"  note: {vocab.note}")
            for value, phrases in vocab.raw.items():
                en = " / ".join(phrases["en"][:3])
                ar = " / ".join(phrases["ar"][:2])
                extra = ""
                if vocab.meta.get(value, {}).get("uk_search_terms"):
                    extra = f" (UK titles use: {', '.join(vocab.meta[value]['uk_search_terms'])})"
                if vocab.meta.get(value, {}).get("excluded"):
                    extra = " (excluded type: specialist review)"
                lines.append(f"  {value} = {en} | {ar}{extra}")
        lines.append(
            "Ambiguous, never stored, always ask for absolute left or right: "
            "driver side, passenger side, جهة السائق, جهة الراكب."
        )
        return "\n".join(lines)


@lru_cache(maxsize=1)
def get_glossary() -> Glossary:
    """Process-wide glossary instance (the YAML is reference data, read-only)."""
    return Glossary.load()
