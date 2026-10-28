"""Generate SHA-256 checksum for compat_ref.csv.

Run:  python scripts/generate_checksum.py
Output: data/reference/compat_ref.sha256
"""

import hashlib
from pathlib import Path

CSV_PATH = Path("data/reference/compat_ref.csv")
CHECKSUM_PATH = Path("data/reference/compat_ref.sha256")


def generate() -> str:
    data = CSV_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    CHECKSUM_PATH.write_text(f"{digest}  compat_ref.csv\n", encoding="utf-8")
    return digest


if __name__ == "__main__":
    d = generate()
    print(f"checksum: {d}")
    print(f"written to: {CHECKSUM_PATH}")
