r"""Import ECDICT exam vocabulary (tag contains `ky`) into the `word` table.

Get ECDICT from https://github.com/skywind3000/ECDICT and put ecdict.csv
into data/. Then run:

    D:\kaoyanruanjian\backend\.venv\Scripts\python.exe scripts\import_ecdict.py data\ecdict.csv

Optional: add --limit 500 for a quick trial.
The import is idempotent: the unique index on `word` prevents duplicates.
Rows imported this way get verified=0 until you check them against the
official exam syllabus.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import transaction  # noqa: E402
from seed import import_ecdict  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Import ECDICT ky words")
    ap.add_argument("csv_path", nargs="?", default="data/ecdict.csv",
                    help="path to ECDICT CSV (default data/ecdict.csv)")
    ap.add_argument("--limit", type=int, default=None, help="stop after N inserts")
    args = ap.parse_args()

    path = Path(args.csv_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        print(f"找不到词库文件：{path}")
        return 1
    print(f"正在从 {path.name} 导入 ky 考研词（{path.stat().st_size // 1048576} MB）…")
    with transaction() as conn:
        result = import_ecdict(conn, path, args.limit)
    print(f"OK: inserted={result['inserted']} skipped={result['skipped']}")
    print("Remember to sample-check against the official syllabus "
          "and set verified=1 for checked rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
