r"""Import the built-in demo word bank into MySQL.

Usage (from anywhere):
    D:\kaoyanruanjian\backend\.venv\Scripts\python.exe scripts\seed_words.py

The backend also runs this automatically on startup, so this script is only
needed when you edit data/kaoyan_core_words.csv and want to refresh manually.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import transaction  # noqa: E402
from seed import DEFAULT_CSV, ensure_words  # noqa: E402


def main():
    with transaction() as conn:
        inserted = ensure_words(conn)
    print(f"OK: {inserted} new word(s) imported from {DEFAULT_CSV}")


if __name__ == "__main__":
    main()
