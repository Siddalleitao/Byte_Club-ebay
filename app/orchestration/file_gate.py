"""CP0 gate — file gate for photos.

Handles:
- E01: type and size check, decode check (corrupt/unsupported → reject)
- E02: HEIC → JPEG conversion and resize
- SHA-256 file hash used as cache key (re-uploads cost nothing)
- E04: perceptual-hash duplicate detection
- E05: blur and brightness score → retake request
- Strip photo metadata (EXIF) per TRD 6 attack surface "File name or metadata"

Nothing in this module calls an LLM.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError

# Try to register HEIC support
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

try:
    import imagehash

    PHASH_AVAILABLE = True
except ImportError:
    PHASH_AVAILABLE = False

# ── Constants ──
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_DIMENSION = 2048  # Resize so longest side ≤ 2048px
JPEG_QUALITY = 85

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
ACCEPTED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
}


@dataclass
class PhotoGateResult:
    """Result of running a file through the photo gate.

    Attributes:
        accepted: whether the file passed the gate
        sha256: SHA-256 hash of the original file bytes (cache key)
        perceptual_hash: perceptual hash string for duplicate detection
        converted_path: path to the converted/resized JPEG (may equal original)
        width, height: dimensions of the processed image
        blur_score: blur detection score (variance of Laplacian approximation)
        brightness: mean brightness (0-255)
        retake_requested: whether blur/brightness triggered a retake request (E05)
        rejection_reason: why the file was rejected (if accepted=False)
        metadata_stripped: whether EXIF metadata was stripped
        edge_cases: list of edge-case IDs triggered
    """

    accepted: bool
    sha256: str = ""
    perceptual_hash: str = ""
    converted_path: str = ""
    width: int = 0
    height: int = 0
    blur_score: float = 0.0
    brightness: float = 0.0
    retake_requested: bool = False
    rejection_reason: str = ""
    metadata_stripped: bool = False
    edge_cases: list[str] = field(default_factory=list)


def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of file bytes. Used as cache key."""
    return hashlib.sha256(data).hexdigest()


def compute_perceptual_hash(img: Image.Image) -> str:
    """Compute perceptual hash for duplicate detection (E04).

    Uses imagehash library if available, otherwise returns empty string.
    """
    if not PHASH_AVAILABLE:
        return ""
    return str(imagehash.phash(img))


def compute_blur_score(img: Image.Image) -> float:
    """Estimate blur using the standard deviation of the Laplacian.

    A low score indicates a blurry image (less detail/edges).
    Uses a Laplacian edge filter and measures the variance of the result.
    A uniform or blurry image has low edge variance.
    """
    gray = img.convert("L")
    # Apply Laplacian edge detection
    edges = gray.filter(ImageFilter_Laplacian())
    stat = ImageStat.Stat(edges)
    variance = stat.var[0]
    # Also compute the std of the original grayscale — a uniform image
    # (solid colour, heavily blurred) has very low std
    gray_stat = ImageStat.Stat(gray)
    gray_std = gray_stat.stddev[0]
    # Combine: blur score = edge_variance * gray_std
    # If either is very low, the image is likely blurry/uniform
    return variance * gray_std


def compute_brightness(img: Image.Image) -> float:
    """Compute mean brightness (0-255). Very low or very high = bad."""
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    return stat.mean[0]


# Laplacian kernel as a PIL ImageFilter
from PIL import ImageFilter


def ImageFilter_Laplacian():
    """Return a Laplacian kernel filter for edge detection."""
    return ImageFilter.Kernel(
        size=(3, 3),
        kernel=[0, 1, 0, 1, -4, 1, 0, 1, 0],
        scale=1,
        offset=0,
    )


# Blur/brightness thresholds (tuned for synthetic placeholder photos)
# The blur score is edge_variance * gray_std.
# A uniform/blurred image has very low edge variance and low gray_std,
# so the product is very low (near 0). A detailed photo has both high.
BLUR_THRESHOLD = 50.0  # product below this = blurry
BRIGHTNESS_MIN = 15.0  # below this = too dark
BRIGHTNESS_MAX = 245.0  # above this = too bright/washed out


def process_file(
    file_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    existing_hashes: dict[str, str] | None = None,
) -> PhotoGateResult:
    """Run a file through the CP0 photo gate.

    Args:
        file_path: Path to the uploaded file.
        output_dir: Directory to save converted JPEGs. If None, uses file's dir.
        existing_hashes: Map of sha256→sku for duplicate detection cache.

    Returns:
        PhotoGateResult with all processing details.
    """
    file_path = Path(file_path)
    edge_cases: list[str] = []

    # ── E01: Type check ──
    ext = file_path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return PhotoGateResult(
            accepted=False,
            rejection_reason=f"Unsupported file type: {ext}",
            edge_cases=["E01"],
        )

    # ── Size check ──
    raw_bytes = file_path.read_bytes()
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        return PhotoGateResult(
            accepted=False,
            rejection_reason=f"File too large: {len(raw_bytes)} bytes (max {MAX_FILE_SIZE_BYTES})",
            edge_cases=["E02"],
        )

    # ── SHA-256 hash (cache key) ──
    sha256 = compute_sha256(raw_bytes)

    # Check cache — if we've seen this exact file, skip processing
    if existing_hashes and sha256 in existing_hashes:
        return PhotoGateResult(
            accepted=True,
            sha256=sha256,
            rejection_reason="",
            edge_cases=[],
        )

    # ── E01: Decode check ──
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()  # force decode to catch corrupt files
    except (UnidentifiedImageError, OSError, ValueError) as e:
        return PhotoGateResult(
            accepted=False,
            sha256=sha256,
            rejection_reason=f"Corrupt or undecodable image: {e}",
            edge_cases=["E01"],
        )

    # ── E02: HEIC → JPEG conversion ──
    is_heic = ext in (".heic", ".heif")
    needs_conversion = is_heic or img.mode not in ("RGB", "L")

    if is_heic and not HEIC_SUPPORTED:
        return PhotoGateResult(
            accepted=False,
            sha256=sha256,
            rejection_reason="HEIC support not available",
            edge_cases=["E02"],
        )

    # Convert to RGB for processing
    if img.mode != "RGB":
        img = img.convert("RGB")

    # ── E02: Resize if too large ──
    w, h = img.size
    if max(w, h) > MAX_DIMENSION:
        ratio = MAX_DIMENSION / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        edge_cases.append("E02")

    if is_heic:
        edge_cases.append("E02")

    # ── Strip EXIF metadata (TRD 6: "metadata stripped") ──
    # Saving without exif= parameter strips metadata in PIL
    metadata_stripped = True

    # ── Save as JPEG ──
    if output_dir is None:
        output_dir = file_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jpeg_path = output_dir / f"{file_path.stem}_processed.jpg"
    img.save(str(jpeg_path), "JPEG", quality=JPEG_QUALITY, exif=b"")

    # ── E04: Perceptual hash for duplicate detection ──
    phash = compute_perceptual_hash(img)

    # ── E05: Blur and brightness scoring ──
    blur_score = compute_blur_score(img)
    brightness = compute_brightness(img)

    retake_requested = False
    if blur_score < BLUR_THRESHOLD:
        retake_requested = True
        edge_cases.append("E05")
    if brightness < BRIGHTNESS_MIN or brightness > BRIGHTNESS_MAX:
        retake_requested = True
        if "E05" not in edge_cases:
            edge_cases.append("E05")

    return PhotoGateResult(
        accepted=True,
        sha256=sha256,
        perceptual_hash=phash,
        converted_path=str(jpeg_path),
        width=img.size[0],
        height=img.size[1],
        blur_score=blur_score,
        brightness=brightness,
        retake_requested=retake_requested,
        metadata_stripped=metadata_stripped,
        edge_cases=edge_cases,
    )


def check_duplicate(
    phash: str,
    existing_phashes: dict[str, str],
    *,
    threshold: int = 5,
) -> str | None:
    """Check if a perceptual hash matches an existing one (E04).

    Args:
        phash: The perceptual hash to check.
        existing_phashes: Map of phash→sku for existing photos.
        threshold: Hamming distance threshold for a match.

    Returns:
        The matching SKU if duplicate found, None otherwise.
    """
    if not phash or not PHASH_AVAILABLE:
        return None

    for existing_phash, sku in existing_phashes.items():
        if not existing_phash:
            continue
        try:
            h1 = imagehash.hex_to_hash(phash)
            h2 = imagehash.hex_to_hash(existing_phash)
            distance = h1 - h2
            if distance <= threshold:
                return sku
        except (ValueError, TypeError):
            continue

    return None
