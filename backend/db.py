"""Database access for the kaoyan vocabulary app.

Credentials live ONLY in backend/.env (git-ignored). The database name is
hard-locked to kaoyanruanjian so all runtime data lands in one place.
"""

import os
import queue
import threading
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

EXPECTED_DB = "kaoyanruanjian"
DB_NAME = os.getenv("DB_NAME", EXPECTED_DB)
if DB_NAME != EXPECTED_DB:
    raise RuntimeError(
        f"DB_NAME must be {EXPECTED_DB}, got {DB_NAME!r}. "
        "All data must stay in the single kaoyanruanjian database."
    )

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "kaoyan_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


def connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
    )


# A single handshake (TCP + MySQL auth) costs 18-49 ms on this machine, and
# every endpoint used to pay it once per request. A small fixed pool removes
# that cost while capping the number of MySQL sessions the app can open.
# pymysql connections are NOT thread-safe, but the pool only ever hands one
# connection to one thread at a time, so no connection is shared concurrently.
POOL_SIZE = max(1, int(os.getenv("DB_POOL_SIZE", "16")))
POOL_WAIT = float(os.getenv("DB_POOL_WAIT", "15"))


class ConnectionPool:
    """LIFO pool of pymysql connections with liveness ping on checkout."""

    def __init__(self, size):
        self.size = size
        self._idle = queue.LifoQueue(maxsize=size)
        self._slots = threading.BoundedSemaphore(size)
        self._lock = threading.Lock()
        self.opened = 0      # connections created since start
        self.reused = 0      # checkouts served from an idle connection
        self.reconnected = 0  # idle connections that had died and were replaced
        self.waited = 0      # checkouts that had to wait for a free slot

    def acquire(self):
        # The semaphore is acquired BEFORE the idle queue so a connection can
        # never be checked out twice: at most `size` checkouts exist at once.
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self.waited += 1
            if not self._slots.acquire(timeout=POOL_WAIT):
                raise RuntimeError(
                    f"数据库连接池已满（{self.size} 个连接被占用超过 {POOL_WAIT:.0f} 秒），"
                    "请稍后重试"
                )
        try:
            conn = self._idle.get_nowait()
        except queue.Empty:
            conn = None
        if conn is not None:
            try:
                # reconnect=True transparently replaces a session killed by
                # MySQL's wait_timeout; that is the normal idle case here.
                conn.ping(reconnect=True)
                with self._lock:
                    self.reused += 1
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                with self._lock:
                    self.reconnected += 1
        try:
            conn = connect()
        except Exception:
            self._slots.release()
            raise
        with self._lock:
            self.opened += 1
        return conn

    def release(self, conn, broken=False):
        try:
            if conn is None:
                return
            if broken:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            try:
                # A recycled connection must never carry an open transaction.
                conn.rollback()
                self._idle.put_nowait(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        finally:
            self._slots.release()

    def stats(self):
        with self._lock:
            return {"size": self.size, "idle": self._idle.qsize(),
                    "opened": self.opened, "reused": self.reused,
                    "reconnected": self.reconnected, "waited": self.waited}


pool = ConnectionPool(POOL_SIZE)


@contextmanager
def transaction():
    """One request = one transaction. Progress update and answer log commit together.

    The connection comes from the pool and always goes back to it, including on
    error paths; a connection whose rollback failed is discarded rather than
    recycled in an unknown state.
    """
    conn = pool.acquire()
    broken = False
    try:
        conn.begin()
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        pool.release(conn, broken=broken)


@contextmanager
def reader():
    """Pooled connection for read-only work (auth lookup, health probe).

    Avoids opening a fresh MySQL session per request just to run one SELECT;
    no explicit BEGIN, and release() rolls back so nothing is left dangling.
    """
    conn = pool.acquire()
    broken = False
    try:
        yield conn
    except Exception:
        broken = True
        raise
    finally:
        pool.release(conn, broken=broken)


def query(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def one(conn, sql, args=()):
    rows = query(conn, sql, args)
    return rows[0] if rows else None


def execute(conn, sql, args=()):
    with conn.cursor() as cur:
        return cur.execute(sql, args)


def detach_for_llm(conn):
    """End the current read transaction before a slow network/LLM call.

    Generation paths first read what they need (existing rows, word lists),
    then call an LLM, then write. Holding a transaction open across the network
    call pins a pooled connection and its locks for tens of seconds when many
    users generate at once. Committing here releases those locks; the writes
    that follow open a fresh transaction, protected by UNIQUE keys so a racing
    duplicate generate cannot create two articles. Idempotent if there is no
    active transaction.
    """
    conn.commit()


def split_sql(text):
    """Split schema.sql into single statements, dropping -- line comments."""
    statements, buf = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf)
            for part in chunk.split(";"):
                if part.strip():
                    statements.append(part.strip())
            buf = []
    return statements


def _column_meta(cur, table, column):
    cur.execute(
        """SELECT COUNT(*) AS c, MAX(is_nullable) AS nullable
           FROM information_schema.columns
           WHERE table_schema=%s AND table_name=%s AND column_name=%s""",
        (DB_NAME, table, column),
    )
    return cur.fetchone()


def ensure_columns(cur):
    """Incremental migration. Only ADD COLUMN / MODIFY to NULL - never drops data.

    The stage-1~3 deployment already has live rows in daily_plan/progress/
    answer_log. Every statement below is guarded by an information_schema check,
    so restarting the backend is always safe and idempotent.
    """
    additions = [
        # Multi-book shelves: legacy rows all belong to book 1 (考研英语).
        ("user", "current_book_id", "INT NOT NULL DEFAULT 1"),
        ("daily_plan", "book_id", "INT NOT NULL DEFAULT 1"),
        ("progress", "book_id", "INT NOT NULL DEFAULT 1"),
        ("answer_log", "book_id", "INT NOT NULL DEFAULT 1"),
        ("article", "book_id", "INT NOT NULL DEFAULT 1"),
        ("chat_session", "book_id", "INT NOT NULL DEFAULT 1"),
        ("user", "sample_cursor", "INT NOT NULL DEFAULT 0"),
        ("answer_log", "kind", "VARCHAR(16) NOT NULL DEFAULT 'word'"),
        ("answer_log", "question_id", "INT NULL"),
        ("answer_log", "article_id", "INT NULL"),
        ("answer_log", "week_key", "VARCHAR(16) NULL"),
        ("answer_log", "score", "DECIMAL(5,2) NULL"),
        ("answer_log", "scored", "TINYINT NOT NULL DEFAULT 0"),
        ("answer_log", "is_late", "TINYINT NOT NULL DEFAULT 0"),
        ("article", "scene", "VARCHAR(32) NULL"),
        ("progress", "review_stage", "TINYINT NOT NULL DEFAULT 0"),
        ("progress", "last_review_at", "DATETIME NULL"),
        ("progress", "next_due_at", "DATE NULL"),
        # review_round: 0 = no in-flight review sub-step; 1 = "认识" done
        # (next: 听写), 2 = "听写" done (next: 默写). Reset to 0 after a full
        # pass or a failure. Drives the three-step Ebbinghaus review.
        ("progress", "review_round", "TINYINT NOT NULL DEFAULT 0"),
        ("progress", "cloze_wrong", "TINYINT NOT NULL DEFAULT 0"),
        # Admin accounts live in their own AUTO_INCREMENT space, so an admin id
        # can collide with a learner id. `admin.user_id` points at a private
        # placeholder row in `user` that only holds that admin's study data;
        # `user.shadow` marks those rows so dashboards ignore them.
        ("user", "shadow", "TINYINT NOT NULL DEFAULT 0"),
        ("admin", "user_id", "INT NULL"),
    ]
    for table, column, ddl in additions:
        meta = _column_meta(cur, table, column)
        if not meta or meta["c"] == 0:
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")

    # The old mandatory context-cloze gate was removed. Any batch still stuck
    # in stage='cloze' is finalized here so those words enter review normally,
    # then the ENUM is narrowed back to learning/memorized/mastered.
    cur.execute("SELECT id, user_id, book_id FROM `daily_plan` WHERE stage='cloze'")
    for row in cur.fetchall() or []:
        cur.execute(
            """UPDATE `daily_plan`
               SET stage='memorized', memorized_at=COALESCE(memorized_at, NOW())
               WHERE id=%s""", (row["id"],))
        cur.execute(
            """UPDATE `progress`
               SET stage='memorized', review_stage=0, review_round=0,
                   next_due_at=DATE_ADD(CURDATE(), INTERVAL 1 DAY)
               WHERE user_id=%s AND book_id=%s AND daily_plan_id=%s AND round=5""",
            (row["user_id"], row["book_id"], row["id"]))
    cur.execute(
        """SELECT column_type FROM information_schema.columns
           WHERE table_schema=%s AND table_name='daily_plan' AND column_name='stage'""",
        (DB_NAME,),
    )
    stage_col = cur.fetchone()
    stage_type = str((stage_col or {}).get("column_type")
                     or (stage_col or {}).get("COLUMN_TYPE") or "")
    if stage_type and "cloze" in stage_type:
        cur.execute(
            """ALTER TABLE `daily_plan` MODIFY COLUMN `stage`
               ENUM('learning','memorized','mastered') NOT NULL DEFAULT 'learning'"""
        )

    # Stage 1~3 made these NOT NULL because every answer belonged to a word.
    # Reading-comprehension answers have no word/plan/progress, so relax them.
    relax = [
        ("answer_log", "word_id", "INT NULL"),
        ("answer_log", "daily_plan_id", "INT NULL"),
        ("answer_log", "progress_id", "INT NULL"),
        ("answer_log", "round", "TINYINT NULL"),
    ]
    for table, column, ddl in relax:
        meta = _column_meta(cur, table, column)
        if meta and meta["c"] > 0 and meta["nullable"] == "NO":
            cur.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {ddl}")

    # Backfill: every admin needs its own `user` row so learner endpoints never
    # resolve an admin id to a stranger's progress. Shadow rows have an empty
    # password (cannot log in) and shadow=1 (hidden from the admin dashboards).
    cur.execute("SELECT id, username FROM `admin` WHERE user_id IS NULL")
    for a in cur.fetchall() or []:
        uname = f"#admin:{a['username']}"
        cur.execute("SELECT id FROM `user` WHERE username=%s", (uname,))
        row = cur.fetchone()
        if row:
            uid = int(row["id"])
            cur.execute("UPDATE `user` SET shadow=1 WHERE id=%s", (uid,))
        else:
            cur.execute(
                "INSERT INTO `user` (`username`,`password_hash`,`shadow`) VALUES (%s,'',1)",
                (uname,),
            )
            uid = int(cur.lastrowid)
        cur.execute("UPDATE `admin` SET user_id=%s WHERE id=%s", (uid, a["id"]))


def _index_exists(cur, table, index):
    cur.execute(
        """SELECT COUNT(*) AS c FROM information_schema.statistics
           WHERE table_schema=%s AND table_name=%s AND index_name=%s""",
        (DB_NAME, table, index),
    )
    return bool(cur.fetchone()["c"])


def _index_columns(cur, table, index):
    cur.execute(
        """SELECT column_name AS c FROM information_schema.statistics
           WHERE table_schema=%s AND table_name=%s AND index_name=%s
           ORDER BY seq_in_index""",
        (DB_NAME, table, index),
    )
    return [r["c"] for r in cur.fetchall()]


def _rebuild_unique(cur, table, old_name, new_name, columns):
    """Replace an old UNIQUE key with the multi-book version, idempotently."""
    wanted = list(columns)
    old_cols = _index_columns(cur, table, old_name)
    new_cols = _index_columns(cur, table, new_name) if new_name != old_name else []
    if old_name == new_name and old_cols == wanted:
        return
    if new_cols == wanted:
        return
    if old_cols:
        cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{old_name}`")
    if new_cols:
        cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{new_name}`")
    col_sql = ",".join(f"`{c}`" for c in wanted)
    cur.execute(
        f"ALTER TABLE `{table}` ADD UNIQUE KEY `{new_name}` ({col_sql})"
    )


def ensure_indexes(cur):
    """Idempotent UNIQUE indexes added after stage 1~3 shipped.

    exam: one answer per (user, question) — the grade path is idempotent and
    duplicate exam rows corrupt the score summary. `question_id` is NULL for
    word answers, so the UNIQUE key only constrains real exam rows.
    Note: word answers legitimately repeat (a wrong spell is retried, r4 reveal
    then self-assess), so we do NOT add a uniqueness constraint there — the
    mark_done() rowcount guard handles concurrent duplicate completions.
    """
    if not _index_exists(cur, "answer_log", "uk_exam_question"):
        cur.execute(
            """ALTER TABLE `answer_log`
               ADD UNIQUE KEY `uk_exam_question` (`user_id`,`question_id`)"""
        )
    if not _index_exists(cur, "question", "uk_question_sort"):
        cur.execute(
            """ALTER TABLE `question`
               ADD UNIQUE KEY `uk_question_sort` (`article_id`,`sort_no`)"""
        )

    # Multi-book shelves: widen uniqueness so one word can be studied
    # independently in each book, and one week can hold an article per book.
    _rebuild_unique(cur, "daily_plan", "uk_user_date", "uk_user_book_date",
                    ("user_id", "book_id", "study_date"))
    _rebuild_unique(cur, "progress", "uk_user_word_round",
                    "uk_user_book_word_round",
                    ("user_id", "book_id", "word_id", "round"))
    _rebuild_unique(cur, "article", "uk_weekly_article", "uk_weekly_article",
                    ("user_id", "kind", "week_key", "seq", "book_id"))
    if not _index_exists(cur, "progress", "idx_fetch_book"):
        cur.execute(
            """ALTER TABLE `progress` ADD INDEX `idx_fetch_book`
               (`user_id`,`book_id`,`status`,`daily_plan_id`,`id`)"""
        )


def init_schema():
    """Idempotent: safe to run on every backend startup."""
    sql_text = (BASE / "schema.sql").read_text(encoding="utf-8")
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            for stmt in split_sql(sql_text):
                cur.execute(stmt)
            ensure_columns(cur)
            ensure_indexes(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
