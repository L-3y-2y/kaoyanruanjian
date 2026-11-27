r"""Migrate the existing database to the multi-book schema (idempotent).

Run from the project root:

    backend\.venv\Scripts\python.exe scripts\migrate_books.py

The backend also runs the same migration automatically on startup; this
script is for an explicit, verbose migration + a post-migration report.

All legacy rows are assigned to book 1 (考研英语). No row is ever deleted.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import connect, init_schema, query  # noqa: E402


def report(conn):
    print("=== 书籍目录 ===")
    for row in query(conn, "SELECT id, code, name, tag_filter FROM `book` ORDER BY sort_no"):
        print(f"  {row['id']}. {row['name']} ({row['code']}) tag={row['tag_filter']}")

    print("\n=== 各标签词数（一个词可属于多本书）===")
    for tag in ("ky", "cet4", "cet6", "gk"):
        pattern = "%" + tag + "%"
        row = query(conn, "SELECT COUNT(*) AS c FROM `word` WHERE `tag` LIKE %s", (pattern,))[0]
        print(f"  {tag}: {row['c']}")

    print("\n=== 各表书籍分布 ===")
    row = query(conn, "SELECT current_book_id, COUNT(*) AS c FROM `user` GROUP BY current_book_id")[0]
    print(f"  user.current_book_id={row['current_book_id']}: {row['c']} 个账号")
    for table in ("daily_plan", "progress", "article", "answer_log", "chat_session"):
        rows = query(conn, f"SELECT book_id, COUNT(*) AS c FROM `{table}` GROUP BY book_id ORDER BY book_id")
        print(f"  {table}: " + ", ".join(f"book {r['book_id']}={r['c']}" for r in rows))

    print("\n=== 唯一键检查 ===")
    for table, index in (
        ("daily_plan", "uk_user_book_date"),
        ("progress", "uk_user_book_word_round"),
        ("article", "uk_weekly_article"),
    ):
        rows = query(
            conn,
            """SELECT column_name AS c FROM information_schema.statistics
               WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s
               ORDER BY seq_in_index""",
            (table, index),
        )
        cols = ",".join(r["c"] for r in rows)
        print(f"  {table}.{index}: {cols or 'MISSING'}")


def main():
    print("正在执行多书籍结构迁移（幂等，不删除任何数据）…")
    init_schema()
    conn = connect()
    try:
        report(conn)
    finally:
        conn.close()
    print("\n迁移完成。老数据均归属 book 1（考研英语）。")


if __name__ == "__main__":
    main()
