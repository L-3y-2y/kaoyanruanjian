"""Database access for the kaoyan vocabulary app.

Credentials live ONLY in backend/.env (git-ignored). The database name is
hard-locked to kaoyanruanjian so all runtime data lands in one place.
"""

import os
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


@contextmanager
def transaction():
    """One request = one transaction. Progress update and answer log commit together."""
    conn = connect()
    try:
        conn.begin()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    ]
    for table, column, ddl in additions:
        meta = _column_meta(cur, table, column)
        if not meta or meta["c"] == 0:
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")

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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
