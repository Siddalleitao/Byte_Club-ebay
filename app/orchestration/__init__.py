"""PartProof orchestration — pipeline coordination.

CP0 gate modules:
- text_normaliser: Unicode NFKC, strip control chars, Arabic-Indic digits, encoding retry
- file_gate: photo type/size/decode, HEIC→JPEG, hash, pHash, blur/brightness
- injection_scan: English + Arabic injection pattern scan, quarantine
- spreadsheet_parser: CSV/XLSX parsing, header mapping, duplicate SKU flag
- sku_linker: filename pattern + spreadsheet ref, manual-assign tray
- privacy_mask: phone/name/VIN/plate masking, face blur
- cp0_pipeline: orchestrates all CP0 steps, creates Evidence rows
"""

from app.orchestration.cp0_pipeline import CP0Result, EvidenceRecord, process_uploads, save_evidence_to_db
from app.orchestration.file_gate import PhotoGateResult, process_file, check_duplicate
from app.orchestration.injection_scan import InjectionHit, InjectionScanResult, scan
from app.orchestration.privacy_mask import MaskResult, mask_text
from app.orchestration.sku_linker import SkuLinkResult, link_files_to_skus, extract_sku_from_filename
from app.orchestration.spreadsheet_parser import ParsedSpreadsheet, parse_spreadsheet, parse_csv, parse_xlsx
from app.orchestration.text_normaliser import NormalisedText, normalise, detect_language

__all__ = [
    "CP0Result",
    "EvidenceRecord",
    "process_uploads",
    "save_evidence_to_db",
    "PhotoGateResult",
    "process_file",
    "check_duplicate",
    "InjectionHit",
    "InjectionScanResult",
    "scan",
    "MaskResult",
    "mask_text",
    "SkuLinkResult",
    "link_files_to_skus",
    "extract_sku_from_filename",
    "ParsedSpreadsheet",
    "parse_spreadsheet",
    "parse_csv",
    "parse_xlsx",
    "NormalisedText",
    "normalise",
    "detect_language",
]
