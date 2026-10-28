"""CP0 gate — injection scanner.

Scans all ingested text for prompt-injection patterns in English and Arabic.
Hits are quarantined with the triggering text stored and logged, and are
never passed on as instructions (Rule f: ingested text is data, never
instructions; TRD 6 defence layer 4; E53).

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InjectionHit:
    """A single injection pattern match.

    Attributes:
        pattern_id: which pattern matched
        pattern_text: the regex or keyword that matched
        matched_text: the actual text that triggered the match
        language: 'en' or 'ar'
        start: start position in the scanned text
        end: end position in the scanned text
    """

    pattern_id: str
    pattern_text: str
    matched_text: str
    language: str
    start: int
    end: int


@dataclass
class InjectionScanResult:
    """Result of scanning text for injection patterns.

    Attributes:
        clean: whether no patterns matched
        hits: list of InjectionHit objects
        quarantined_text: the original text, stored for quarantine (Rule f)
    """

    clean: bool
    hits: list[InjectionHit] = field(default_factory=list)
    quarantined_text: str = ""


# ── English injection patterns (TRD 6 attack surface examples) ──
ENGLISH_PATTERNS: list[tuple[str, str]] = [
    ("EN-01", r"ignore\s+(previous|prior|all|above)\s+instructions?"),
    ("EN-02", r"ignore\s+(previous|prior|all|above)\s+prompts?"),
    ("EN-03", r"you\s+are\s+now\s+in\s+(admin|root|developer|system)\s+mode"),
    ("EN-04", r"disregard\s+(all|previous|prior)\s+(instructions?|rules?|guidelines?)"),
    ("EN-05", r"act\s+as\s+(if\s+you\s+are\s+)?(admin|root|system|developer)"),
    ("EN-06", r"export\s+(everything|all|now)"),
    ("EN-07", r"mark\s+all\s+(parts?|items?)\s+as\s+(tested|ready|approved|working)"),
    ("EN-08", r"set\s+(status|all\s+statuses)\s+to\s+(approved|ready|exported)"),
    ("EN-09", r"system\s*:\s*(tested|approved|ready|export)"),
    ("EN-10", r"approve\s+all"),
    ("EN-11", r"write\s+(fully\s+working|genuine\s+oem|brand\s+new|no\s+defects?)"),
    ("EN-12", r"new\s+instruction\s*:"),
    ("EN-13", r"override\s+(all\s+)?(checks?|guardrails?|rules?|safety)"),
    ("EN-14", r"(guaranteed|guarantee)\s+fit"),
    ("EN-15", r"fits\s+all"),
]

# ── Arabic injection patterns ──
# Arabic for "ignore previous instructions": تجاهل التعليمات السابقة
# Arabic for "mark all parts as ready": حدد جميع القطع كجاهزة
# Arabic for "you are now admin": أنت الآن في وضع المسؤول
ARABIC_PATTERNS: list[tuple[str, str]] = [
    ("AR-01", r"تجاهل\s+(التعليمات|الأوامر)\s+(السابقة|السابق)"),
    ("AR-02", r"تجاهل\s+(كل|جميع)\s+(التعليمات|الأوامر|القواعد)"),
    ("AR-03", r"أنت\s+(الآن|اصبحت)\s+في\s+وضع\s+(المسؤول|الإدارة|النظام)"),
    ("AR-04", r"حدد\s+(جميع|كل)\s+(القطع|الأجزاء)\s+(ك|作为)?(جاهزة|مختبرة|معتمدة|مصادق عليها)"),
    ("AR-05", r"صدّر\s+(الكل|الجميع|كل\s+شيء)"),
    ("AR-06", r"تجاوز\s+(كل\s+)?(القواعد|الفحوصات|القيود|حاجز الأمان)"),
    ("AR-07", r"اضبط\s+(الحالة|كل\s+الحالات)\s+(إلى|ك)\s+(معتمد|جاهز|مصدّر)"),
    ("AR-08", r"نظام\s*:\s*(مختبر|معتمد|جاهز|صدّر)"),
    ("AR-09", r"اكتب\s+(يعمل\s+بالكامل|أصلي|جديد\s+تماماً|لا\s+يوجد\s+عيوب)"),
    ("AR-10", r"ضمان\s+التوافق|يناسب\s+الجميع"),
]

# Compile all patterns
_COMPILED_EN = [(pid, re.compile(pat, re.IGNORECASE)) for pid, pat in ENGLISH_PATTERNS]
_COMPILED_AR = [(pid, re.compile(pat, re.IGNORECASE)) for pid, pat in ARABIC_PATTERNS]


def scan(text: str) -> InjectionScanResult:
    """Scan text for injection patterns in English and Arabic.

    Args:
        text: The text to scan (should be already normalised).

    Returns:
        InjectionScanResult with hits if any patterns matched.
    """
    hits: list[InjectionHit] = []

    # Scan English patterns
    for pattern_id, compiled in _COMPILED_EN:
        for match in compiled.finditer(text):
            hits.append(
                InjectionHit(
                    pattern_id=pattern_id,
                    pattern_text=compiled.pattern,
                    matched_text=match.group(0),
                    language="en",
                    start=match.start(),
                    end=match.end(),
                )
            )

    # Scan Arabic patterns
    for pattern_id, compiled in _COMPILED_AR:
        for match in compiled.finditer(text):
            hits.append(
                InjectionHit(
                    pattern_id=pattern_id,
                    pattern_text=compiled.pattern,
                    matched_text=match.group(0),
                    language="ar",
                    start=match.start(),
                    end=match.end(),
                )
            )

    return InjectionScanResult(
        clean=len(hits) == 0,
        hits=hits,
        quarantined_text=text if hits else "",
    )


def scan_all(texts: dict[str, str]) -> dict[str, InjectionScanResult]:
    """Scan multiple text fields at once.

    Args:
        texts: Map of field_name → text to scan.

    Returns:
        Map of field_name → InjectionScanResult.
    """
    return {field_name: scan(text) for field_name, text in texts.items()}
