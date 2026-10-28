"""Generate synthetic placeholder photos for each part using Pillow.

All images are SYNTHETIC PLACEHOLDERS — simple drawings of side-view mirrors
with labelled regions, visible cracks, and blurry labels as needed per the
trap plan in docs/DECISIONS.md.

Run:  python scripts/generate_photos.py
Output: data/seed/photos/M-XX_pN.jpg  (3 photos per mirror part)
"""

from __future__ import annotations

import csv
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SEED_DIR = Path("data/seed")
PHOTOS_DIR = SEED_DIR / "photos"
INVENTORY_CSV = SEED_DIR / "inventory.csv"

# ── Trap plan: which parts get which photo features ──
# photo_3_crack: M-07 (lower mounting tab crack per brief), M-16 (upper mounting ear crack)
PHOTO_3_CRACK = {"M-07", "M-16"}

# blurry_label: M-07 (label unreadable), M-14 (label blurry/unreadable)
BLURRY_LABEL = {"M-07", "M-14"}

# M-17 is an airbag, not a mirror — draw a different shape
AIRBAG_SKU = "M-17"

# Part numbers for label region (from spreadsheet)
PART_NUMBERS = {
    "M-01": "87910-02A10",
    "M-02": "96302-4AA0A",
    "M-03": "76200-TBA-A02",
    "M-04": "87940-06A10",
    "M-05": "87914-30A20",
    "M-06": "MR495981",
    "M-07": "87910-0????",
    "M-08": "96301-3AA0A",
    "M-09": "",  # missing
    "M-10": "MR585271",
    "M-11": "51162M89K9X03",
    "M-12": "87914-60410",
    "M-13": "87610-3X300",
    "M-14": "",  # blurry/unreadable
    "M-15": "DB3Z-17683-A",
    "M-16": "22951629",
    "M-17": "84510-0K010",
    "M-18": "96301-4AA0A",
    "M-19": "HB1B-69-170",
    "M-20": "5G0-857-507-A",
}

WIDTH, HEIGHT = 800, 600

# Colours
BG = (240, 240, 245)
MIRROR_HOUSING = (60, 60, 70)
MIRROR_GLASS = (100, 130, 160)
LABEL_BG = (255, 255, 230)
LABEL_TEXT = (40, 40, 40)
CRACK_COLOR = (20, 20, 20)
WHITE = (255, 255, 255)


def _font(size: int = 24) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_mirror(img: Image.Image, draw: ImageDraw.ImageDraw, sku: str, photo_num: int, label_text: str):
    """Draw a simplified side-view mirror placeholder."""

    # Housing outline (rounded rectangle approximation)
    hx1, hy1 = 150, 180
    hx2, hy2 = 650, 420
    draw.rounded_rectangle(
        [hx1, hy1, hx2, hy2], radius=30, fill=MIRROR_HOUSING, outline=(30, 30, 30), width=3
    )

    # Mirror glass area
    draw.rounded_rectangle(
        [hx1 + 20, hy1 + 20, hx2 - 60, hy2 - 20],
        radius=20,
        fill=MIRROR_GLASS,
        outline=(50, 80, 110),
        width=2,
    )

    # Mounting tab area (bottom)
    tab_x1 = 300
    tab_x2 = 400
    draw.rectangle(
        [tab_x1, hy2, tab_x2, hy2 + 60], fill=MIRROR_HOUSING, outline=(30, 30, 30), width=2
    )

    # Photo number and SKU at top
    f = _font(28)
    draw.text((20, 15), f"{sku} — photo {photo_num}", fill=(50, 50, 50), font=f)

    # Part-number label region (a small rectangle on housing)
    lbl_x1, lbl_y1 = 500, 200
    lbl_x2, lbl_y2 = 620, 260
    draw.rectangle([lbl_x1, lbl_y1, lbl_x2, lbl_y2], fill=LABEL_BG, outline=(180, 180, 150), width=2)

    if label_text:
        lf = _font(20)
        draw.text((lbl_x1 + 10, lbl_y1 + 15), label_text, fill=LABEL_TEXT, font=lf)
    else:
        lf = _font(16)
        draw.text((lbl_x1 + 10, lbl_y1 + 20), "[no label]", fill=(150, 150, 150), font=lf)

    # Apply crack if needed on photo 3
    if sku in PHOTO_3_CRACK and photo_num == 3:
        _draw_crack(draw, sku)

    # Apply blur to label region if needed
    if sku in BLURRY_LABEL:
        _blur_label(img, lbl_x1, lbl_y1, lbl_x2, lbl_y2)


def _draw_crack(draw: ImageDraw.ImageDraw, sku: str):
    """Draw a crack line on the mounting tab or upper ear."""
    cf = _font(16)
    if sku == "M-07":
        # Crack on lower mounting tab
        draw.line([(310, 430), (330, 460), (350, 430), (370, 460)], fill=CRACK_COLOR, width=2)
        draw.text((280, 465), "crack?", fill=(200, 0, 0), font=cf)
    elif sku == "M-16":
        # Crack on upper mounting ear
        draw.line([(310, 170), (330, 195), (350, 170)], fill=CRACK_COLOR, width=2)
        draw.text((280, 140), "crack?", fill=(200, 0, 0), font=cf)


def _blur_label(img: Image.Image, x1: int, y1: int, x2: int, y2: int):
    """Apply a blur to the label region of the image."""
    # Crop, blur, paste back
    region = img.crop((x1, y1, x2, y2))
    blurred = region.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(blurred, (x1, y1))


def _draw_airbag(img: Image.Image, draw: ImageDraw.ImageDraw, sku: str, photo_num: int, label_text: str):
    """Draw a simple airbag module placeholder (for M-17)."""
    draw.ellipse([200, 150, 600, 450], fill=(50, 50, 55), outline=(20, 20, 20), width=3)
    draw.ellipse([250, 200, 550, 400], fill=(40, 40, 45), outline=(25, 25, 25), width=2)
    f = _font(28)
    draw.text((20, 15), f"{sku} — photo {photo_num}", fill=(50, 50, 50), font=f)
    f2 = _font(20)
    draw.text((280, 270), "AIRBAG", fill=(255, 100, 100), font=f2)
    # Label region
    draw.rectangle([300, 380, 500, 430], fill=LABEL_BG, outline=(180, 180, 150), width=2)
    if label_text:
        draw.text((310, 390), label_text, fill=LABEL_TEXT, font=_font(18))


def generate_all() -> list[Path]:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    generated = []

    for sku in sorted(PART_NUMBERS.keys()):
        label_text = PART_NUMBERS[sku]
        for photo_num in (1, 2, 3):
            img = Image.new("RGB", (WIDTH, HEIGHT), BG)
            draw = ImageDraw.Draw(img)

            if sku == AIRBAG_SKU:
                _draw_airbag(img, draw, sku, photo_num, label_text)
            else:
                _draw_mirror(img, draw, sku, photo_num, label_text)

            # Photo 2 for M-16: crack on upper ear (shown on photo 2 per note)
            if sku == "M-16" and photo_num == 2:
                # The crack is drawn on photo 3 for M-16; but note says photo 2.
                # We already drew it on photo 3. Let's also show it on photo 2.
                _draw_crack(draw, sku)

            out_path = PHOTOS_DIR / f"{sku}_p{photo_num}.jpg"
            img.save(str(out_path), "JPEG", quality=85)
            generated.append(out_path)

    return generated


if __name__ == "__main__":
    paths = generate_all()
    print(f"Generated {len(paths)} placeholder photos in {PHOTOS_DIR}")
    for p in paths:
        print(f"  {p}")
