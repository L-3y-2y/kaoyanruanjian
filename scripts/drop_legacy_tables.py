"""Drop the five dead tables left over from earlier versions of this project.

    python scripts/drop_legacy_tables.py          # 只打印，不动手
    python scripts/drop_legacy_tables.py --yes    # 真的 DROP

`schema.sql` 只定义 16 张表，但库里有 21 张：多出来的是 checkins / learn_cloze /
study_tasks / subjects / word_scene——早期"打卡 / 关卡 / 任务 / 学科 / 词场景"那版
设计的残留，全仓库 grep 不到任何引用，业务已完全不用。

Dropping them only makes `GET /api/health` 的 tables 计数回到 16，功能没有任何变化。
这不是自动迁移的一部分：删表不可逆，必须人工确认，所以默认 dry-run。
"""

import sys

sys.path.insert(0, "backend")

from db import connect, execute, query  # noqa: E402

LEGACY = ("checkins", "learn_cloze", "study_tasks", "subjects", "word_scene")


def main() -> int:
    confirm = "--yes" in sys.argv
    conn = connect()
    try:
        alive = {list(r.values())[0] for r in query(conn, "SHOW TABLES")}
        print(f"current tables: {len(alive)}")
        for table in LEGACY:
            if table not in alive:
                print(f"  - {table:14s} already gone")
                continue
            rows = query(conn, f"SELECT COUNT(*) AS c FROM `{table}`")[0]["c"]
            if confirm:
                execute(conn, f"DROP TABLE `{table}`")
                print(f"  x {table:14s} dropped ({rows} rows)")
            else:
                print(f"  ? {table:14s} still present, {rows} rows "
                      f"(would drop; add --yes)")
        if confirm:
            conn.commit()
        left = {list(r.values())[0] for r in query(conn, "SHOW TABLES")}
        print(f"tables now: {len(left)}")
    finally:
        conn.close()
    if not confirm:
        print("\nNothing changed. Re-run with --yes to drop the tables above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
