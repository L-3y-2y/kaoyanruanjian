r"""Import ECDICT exam words for the four books (ky / cet4 / cet6 / gk).

The ECDICT `tag` field carries several exam tags per word, so one word can
serve several books without duplication. INSERT IGNORE means words already in
the database keep their original data.

    # preview per-tag counts, no DB writes
    backend\.venv\Scripts\python.exe scripts\import_books.py --dry-run

    # import cet4 / cet6 / gk words missing from the current ky-only bank
    backend\.venv\Scripts\python.exe scripts\import_books.py

    # a specific subset
    backend\.venv\Scripts\python.exe scripts\import_books.py --tags cet4,gk
"""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import connect, query, transaction  # noqa: E402
from seed import import_ecdict  # noqa: E402

ALL_TAGS = ("ky", "cet4", "cet6", "gk")


def csv_tag_counts(path):
    counts = {t: 0 for t in ALL_TAGS}
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            tags = set((row.get("tag") or "").split())
            for t in ALL_TAGS:
                if t in tags:
                    counts[t] += 1
    return rows, counts


def db_tag_counts(conn):
    counts = {}
    for t in ALL_TAGS:
        counts[t] = query(
            conn,
            "SELECT COUNT(*) AS c FROM `word` WHERE `tag` LIKE %s",
            ("%" + t + "%",),
        )[0]["c"]
    return counts


def main():
    ap = argparse.ArgumentParser(description="Import ECDICT words by exam tag")
    ap.add_argument("csv_path", nargs="?", default="data/ecdict.csv")
    ap.add_argument("--tags", default="cet4,cet6,gk",
                    help="comma-separated exam tags (default cet4,cet6,gk)")
    ap.add_argument("--dry-run", action="store_true", help="only count, no writes")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    path = Path(args.csv_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        print(f"找不到词库文件：{path}")
        return 1

    wanted = [t.strip() for t in args.tags.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in ALL_TAGS]
    if unknown:
        print(f"未知标签：{unknown}，可选：{list(ALL_TAGS)}")
        return 1

    print(f"正在扫描 {path.name}（{path.stat().st_size // 1048576} MB）…")
    total, csv_counts = csv_tag_counts(path)
    print(f"CSV 总行数：{total}")
    print("CSV 各标签词数：" + ", ".join(f"{t}={csv_counts[t]}" for t in ALL_TAGS))

    if args.dry_run:
        print("（dry-run：未写入数据库）")
        return 0

    with transaction() as conn:
        before = db_tag_counts(conn)
        result = import_ecdict(conn, path, limit=args.limit, tags=wanted,
                               source="ECDICT-exam")
    print(f"\n导入标签 {wanted}：新增 {result['inserted']} 词，跳过 {result['skipped']} 行")

    conn = connect()
    try:
        after = db_tag_counts(conn)
    finally:
        conn.close()
    print("\n数据库各标签词数（导入前 → 导入后）：")
    for t in ALL_TAGS:
        print(f"  {t}: {before[t]} → {after[t]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
