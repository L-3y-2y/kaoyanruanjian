r"""Friday-evening weekly reading publisher (B5).

The backend already runs a lightweight scheduler and also regenerates on page
open, so this script is optional. It exists for a Windows Scheduled Task:

    schtasks /Create /TN "KaoyanWeeklyReading" /SC WEEKLY /D FRI /ST 18:05 ^
      /TR "\"D:\kaoyanruanjian\backend\.venv\Scripts\python.exe\" \"D:\kaoyanruanjian\scripts\weekly_publish.py\""

Run manually at any time to check/repair the current week. Idempotent: an
already published week is never regenerated.
"""

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from content import ensure_weekly, week_bounds, week_key_of  # noqa: E402
from db import init_schema, query, transaction  # noqa: E402


def main():
    init_schema()
    key = week_key_of(date.today())
    _, _, publish_at, _ = week_bounds(key)
    if datetime.now() < publish_at:
        print(f"{key} 尚未到发布时间 {publish_at}，本次不生成。")
        return 0
    with transaction() as conn:
        ids = [row["id"] for row in
               query(conn, "SELECT id FROM `user` WHERE password_hash<>''")] + [1]
        for uid in ids:
            result = ensure_weekly(conn, uid, key)
            print(f"user {uid}: {result['status']}, "
                  f"{len(result['articles'])} 篇（周次 {key}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
