"""Run the 25-prompt red-team suite and print the pass rate (TRD 6, test plan).

Each prompt goes through the real code path (chat concierge, CP0 upload gate, or intake + CP1) on
a fresh seeded database in a temporary folder; the project database is not touched.

Run:  python scripts/redteam.py
Exit code 0 when every prompt passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.redteam.harness import report, run_suite  # noqa: E402

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    outcomes = run_suite()
    print(report(outcomes))
    sys.exit(0 if all(o.passed for o in outcomes) else 1)
