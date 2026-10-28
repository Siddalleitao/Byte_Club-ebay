"""Convenience launcher for the Streamlit part workspace (board, part detail, quarantine) on port 8502."""

import subprocess
import sys

if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "ui/workspace.py", "--server.port", "8502"],
    )
