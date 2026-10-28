"""CP0 gate — personal-data masking.

Masks personal data in ingested content:
- E52: Phone numbers and personal names in notes
- E51: Number plates, VINs and faces in photos blurred before display and export

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter


# ── Phone number patterns ──
# UAE phone: +971-50-123-4567, 050-123-4567, +971501234567
PHONE_PATTERNS = [
    re.compile(r"\+971[\s\-]?(\d)[\s\-]?(\d{3})[\s\-]?(\d{4})", re.IGNORECASE),
    re.compile(r"\+971[\s\-]?(\d{2})[\s\-]?(\d{3})[\s\-]?(\d{4})", re.IGNORECASE),
    # Generic international: +XX-XXX-XXX-XXXX
    re.compile(r"\+\d{1,3}[\s\-]?\d{1,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}"),
    # UAE local: 0XX-XXX-XXXX or 0XX XXX XXXX (must start with 0 and have phone-like structure)
    re.compile(r"\b0\d{2}[\s\-]?\d{3}[\s\-]?\d{4}\b"),
]

# ── VIN pattern (17 chars, alphanumeric excluding I, O, Q) ──
VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)

# ── Number plate patterns ──
# UAE: A-12345 (letter(s) dash digits), Dubai: 1-12345
# UK: AB12 CDE
# These are made specific enough NOT to match phone numbers (phones start with 0 or +)
PLATE_PATTERNS = [
    # UAE style: letter(s) followed by dash/space and digits (but not starting with 0 or +)
    re.compile(r"\b[A-Z]{1,3}[-\s]\d{1,6}\b"),
    # UK style: two letters, two digits, space, three letters
    re.compile(r"\b[A-Z]{2}\d{2}\s[A-Z]{3}\b", re.IGNORECASE),
]

# ── Personal name patterns ──
# Simple heuristic: look for patterns like "contact: Name" or "seller: Name"
# This is intentionally conservative — better to miss some than over-mask
NAME_CONTEXT_PATTERNS = [
    re.compile(r"(?:contact|seller|buyer|name|caller)\s*[:\-]\s*([A-Z][a-z]+\s+[A-Z][a-z]+)", re.IGNORECASE),
    re.compile(r"(?:contact|seller|buyer|name|caller)\s*[:\-]\s*([A-Z][a-z]+)", re.IGNORECASE),
]


@dataclass
class MaskResult:
    """Result of masking personal data in text.

    Attributes:
        masked_text: the text with personal data replaced
        phone_numbers_masked: count of phone numbers masked
        names_masked: count of names masked
        vins_masked: count of VINs masked
        plates_masked: count of number plates masked
    """

    masked_text: str
    phone_numbers_masked: int
    names_masked: int
    vins_masked: int
    plates_masked: int


def mask_text(text: str) -> MaskResult:
    """Mask personal data in text (E52: phones, names; E51: plates, VINs).

    Replaces matched patterns with [REDACTED].
    """
    masked = text
    phone_count = 0
    name_count = 0
    vin_count = 0
    plate_count = 0

    # Mask phone numbers (E52)
    for pattern in PHONE_PATTERNS:
        masked, count = pattern.subn("[PHONE REDACTED]", masked)
        phone_count += count

    # Mask VINs (E51)
    masked, count = VIN_PATTERN.subn("[VIN REDACTED]", masked)
    vin_count += count

    # Mask number plates (E51)
    for pattern in PLATE_PATTERNS:
        masked, count = pattern.subn("[PLATE REDACTED]", masked)
        plate_count += count

    # Mask personal names (E52)
    for pattern in NAME_CONTEXT_PATTERNS:
        matches = pattern.findall(masked)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and len(match) > 1:
                masked = masked.replace(match, "[NAME REDACTED]")
                name_count += 1

    return MaskResult(
        masked_text=masked,
        phone_numbers_masked=phone_count,
        names_masked=name_count,
        vins_masked=vin_count,
        plates_masked=plate_count,
    )


def blur_photo_regions(
    image_path: str | Path,
    *,
    plate_regions: list[tuple[int, int, int, int]] | None = None,
    face_regions: list[tuple[int, int, int, int]] | None = None,
    output_path: str | Path | None = None,
) -> str:
    """Blur specific regions of a photo (E51: plates, VINs, faces).

    Args:
        image_path: path to the image file
        plate_regions: list of (x1, y1, x2, y2) bounding boxes to blur
        face_regions: list of (x1, y1, x2, y2) bounding boxes to blur
        output_path: where to save the blurred image (default: overwrite)

    Returns:
        Path to the blurred image.
    """
    image_path = Path(image_path)
    if output_path is None:
        output_path = image_path.with_suffix(".masked.jpg")
    output_path = Path(output_path)

    img = Image.open(str(image_path))

    # Blur each region
    for regions in [plate_regions or [], face_regions or []]:
        for x1, y1, x2, y2 in regions:
            # Clamp to image bounds
            x1 = max(0, min(x1, img.width))
            y1 = max(0, min(y1, img.height))
            x2 = max(0, min(x2, img.width))
            y2 = max(0, min(y2, img.height))

            if x2 <= x1 or y2 <= y1:
                continue

            # Crop, blur, paste back
            region = img.crop((x1, y1, x2, y2))
            blurred = region.filter(ImageFilter.GaussianBlur(radius=15))
            img.paste(blurred, (x1, y1))

    img.save(str(output_path), "JPEG", quality=85, exif=b"")
    return str(output_path)


def has_personal_data(text: str) -> bool:
    """Quick check whether text contains any personal data patterns."""
    for pattern in PHONE_PATTERNS:
        if pattern.search(text):
            return True
    if VIN_PATTERN.search(text):
        return True
    for pattern in PLATE_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in NAME_CONTEXT_PATTERNS:
        if pattern.search(text):
            return True
    return False
