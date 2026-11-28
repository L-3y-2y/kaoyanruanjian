"""周阅读的定时发布线程（周五晚自动出本周两篇短文）。

从 main.py 抽出来：它和路由无关，只是一个后台循环，lifespan 负责启停。
"""

import logging
import threading
from datetime import datetime

from content import ensure_weekly, week_bounds, week_key_of
from db import one, query, transaction


class WeeklyPublisher:
    """Friday-evening publish loop. Idempotent: existing rows are never replaced."""

    def __init__(self, interval=600):
        self._stop = threading.Event()
        self._thread = None
        self._interval = interval

    def publish_due_week(self):
        now = datetime.now()
        key = week_key_of(now.date())
        _, _, publish_at, _ = week_bounds(key)
        if now < publish_at:
            return
        with transaction() as conn:
            users = query(conn, "SELECT id FROM `user` WHERE password_hash<>''")
            ids = [row["id"] for row in users] + [1]
            books = query(conn, "SELECT * FROM `book` WHERE enabled=1")
            for uid in ids:
                for book in books:
                    # Only publish a shelf the learner has actually studied:
                    # otherwise every startup would burn LLM calls on four books
                    # for accounts that never opened them.
                    studied = one(
                        conn,
                        """SELECT 1 AS c FROM `daily_plan`
                           WHERE user_id=%s AND book_id=%s AND stage='mastered'
                           LIMIT 1""",
                        (uid, book["id"]),
                    )
                    if not studied:
                        continue
                    ensure_weekly(conn, uid, key, book_id=book["id"], book=book)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.publish_due_week()
            except Exception:
                # Open-time fallback in reading_payload still covers it, but log
                # so failures stay observable instead of being silently swallowed.
                logging.exception("weekly article scheduled publish failed")
            self._stop.wait(self._interval)

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="weekly-publish")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
