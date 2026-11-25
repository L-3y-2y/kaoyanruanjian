"""考研背单词 · FastAPI backend (full stage 1-5).

Rules that must never be broken (see docs/design-decisions.md):
  - Progress lives ONLY in MySQL. The frontend never keeps a cursor or index.
  - The next item is found by querying pending rows ordered by cycle_no -> round -> id.
  - Progress update and answer_log insertion share ONE transaction.
  - Every user-data endpoint resolves its identity from the session cookie,
    never from anything the browser sends as a user id.
  - Articles and weekly reading never write back to `progress`.
"""

import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import LOCAL_MODE, current_user, require_admin, router as auth_router
from content import (
    SCENES, SCENE_LABELS, article_full_segments, article_payload,
    article_segments, daily_batches, ensure_daily_articles, ensure_weekly,
    grade_answer, reading_payload, regenerate_article, week_key_of, week_bounds,
)
import dict_lookup
from db import DB_NAME, connect, execute, init_schema, one, query, transaction
from llm import (
    GenerationFailed, NoProvider, available_models, configured as llm_configured,
    current_model, switch_model, test_model,
)
import chat as chat_service
from seed import ensure_words

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
_scheduler_stop = threading.Event()
REVIEW_INTERVALS = (1, 2, 4, 7, 15, 30)


def _publish_due_week():
    """Friday-evening real scheduling (B5). Idempotent thanks to existing rows."""
    now = datetime.now()
    key = week_key_of(now.date())
    _, _, publish_at, _ = week_bounds(key)
    if now < publish_at:
        return
    with transaction() as conn:
        users = query(conn, "SELECT id FROM `user` WHERE password_hash<>''")
        ids = [row["id"] for row in users] + [1]
        for uid in ids:
            ensure_weekly(conn, uid, key)


def _scheduler_loop():
    while not _scheduler_stop.is_set():
        try:
            _publish_due_week()
        except Exception:
            # open-time fallback in reading_payload still covers it, but log so
            # failures stay observable instead of being silently swallowed.
            logging.exception("weekly article scheduled publish failed")
        _scheduler_stop.wait(600)


@asynccontextmanager
async def lifespan(app):
    init_schema()
    with transaction() as conn:
        ensure_words(conn)
    _scheduler_stop.clear()
    worker = threading.Thread(target=_scheduler_loop, daemon=True, name="weekly-publish")
    worker.start()
    # Warm the ECDICT dictionary in the background so the first "show every
    # word" click is instant instead of paying the ~1-2s CSV parse inline.
    threading.Thread(target=dict_lookup.warm, daemon=True, name="dict-warm").start()
    try:
        yield
    finally:
        _scheduler_stop.set()


app = FastAPI(title="考研背单词", version="1.0.0-full", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": "请求参数不合法"})


# ---------------------------------------------------------------- helpers

def norm(value):
    """Case / whitespace normalisation for spelling answers."""
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def pool_size(conn, uid):
    return one(
        conn,
        """SELECT COUNT(*) AS c FROM `word` w
           WHERE NOT EXISTS (
             SELECT 1 FROM `progress` p WHERE p.user_id=%s AND p.word_id=w.id
           )""",
        (uid,),
    )["c"]


def log_answer(conn, uid, p, action, value=None, correct=None, assessment=None):
    execute(
        conn,
        """INSERT INTO `answer_log`
           (user_id, word_id, daily_plan_id, progress_id, round, kind,
            action, user_answer, correct, self_assessment)
           VALUES (%s,%s,%s,%s,%s,'word',%s,%s,%s,%s)""",
        (uid, p["word_id"], p["daily_plan_id"], p["id"], p["round"],
         action, value, correct, assessment),
    )


def mark_done(conn, progress_id):
    # The WHERE ... status='pending' clause is the atomic duplicate-submit
    # guard: only the request that actually flips pending->done may write a
    # completion log. Callers MUST check the returned rowcount.
    return execute(
        conn,
        "UPDATE `progress` SET status='done' WHERE id=%s AND status='pending'",
        (progress_id,),
    )


def require_mark_done(conn, progress_id):
    """Flip pending->done atomically; 0 rows means a concurrent request won."""
    if mark_done(conn, progress_id) == 0:
        raise HTTPException(status_code=409, detail="这题已提交，请勿重复作答")


def review_fail(conn, p):
    """A review sub-step failed: reset the word to day-1 and restart Ebbinghaus."""
    execute(
        conn,
        """UPDATE `progress`
           SET review_round=0, review_stage=0, wrong_count=wrong_count+1,
               next_due_at=DATE_ADD(CURDATE(), INTERVAL %s DAY),
               last_review_at=NOW()
           WHERE id=%s""",
        (REVIEW_INTERVALS[0], p["id"]),
    )


def review_pass(conn, p):
    """A full 3-sub-step review passed: advance to the next Ebbinghaus interval,
    or mark mastered once every interval has been walked."""
    new_stage = int(p["review_stage"] or 0) + 1
    if new_stage >= len(REVIEW_INTERVALS):
        execute(
            conn,
            """UPDATE `progress`
               SET stage='mastered', review_round=0, review_stage=%s,
                   next_due_at=NULL, last_review_at=NOW()
               WHERE id=%s""",
            (new_stage, p["id"]),
        )
        # Mirror the mastered flag onto every round row of this word.
        execute(
            conn,
            "UPDATE `progress` SET stage='mastered' WHERE user_id=%s AND word_id=%s",
            (p["user_id"], p["word_id"]),
        )
    else:
        execute(
            conn,
            """UPDATE `progress`
               SET review_round=0, review_stage=%s,
                   next_due_at=DATE_ADD(CURDATE(), INTERVAL %s DAY),
                   last_review_at=NOW()
               WHERE id=%s""",
            (new_stage, REVIEW_INTERVALS[new_stage], p["id"]),
        )


def plan_totals(conn, uid, plan_id, min_round, max_round):
    row = one(
        conn,
        """SELECT SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                  COUNT(*) AS total
           FROM `progress`
           WHERE user_id=%s AND daily_plan_id=%s AND round BETWEEN %s AND %s""",
        (uid, plan_id, min_round, max_round),
    )
    return {"done": int(row["done"] or 0), "total": int(row["total"] or 0)}


def item_payload(row, plan, counts, kind):
    payload = {
        "kind": kind,
        "progress_id": row["id"],
        "round": row["round"],
        "cycle_no": row["cycle_no"],
        "cycle_count": plan["cycle_count"],
        "cycle_size": row["cycle_size"],
        "done": counts["done"],
        "total": counts["total"],
        "plan_id": plan["id"],
    }
    if kind == "r1":
        payload.update(word=row["word"], phonetic=row["phonetic"],
                       translation=row["translation"], pos=row["pos"],
                       speak_text=row["word"])
    elif kind == "r2":
        payload.update(speak_text=row["word"], phonetic=row["phonetic"])
    elif kind == "r3":
        payload.update(translation=row["translation"], pos=row["pos"],
                       speak_text=row["word"])
    elif kind == "r4":
        payload.update(word=row["word"], phonetic=row["phonetic"])
    elif kind == "r5":
        payload.update(translation=row["translation"], pos=row["pos"])
    return payload


def review_item_payload(row):
    """One due word for the Ebbinghaus review queue.

    sub_step drives the UI: 1 = 认不认识, 2 = 听音默写, 3 = 汉译英默写.
    review_round records how many sub-steps this word has already cleared
    (0 = none yet, 1 = 认识 done, 2 = 听写 done).
    """
    return {
        "kind": "review",
        "progress_id": row["id"],
        "sub_step": int(row["review_round"] or 0) + 1,
        "review_stage": int(row["review_stage"] or 0),
        "word": row["word"],
        "phonetic": row["phonetic"],
        "translation": row["translation"],
        "pos": row["pos"],
        "speak_text": row["word"],
    }


NEXT_ITEM_SQL = """
SELECT p.id, p.round, p.cycle_no, w.word, w.phonetic, w.translation, w.pos,
       (SELECT COUNT(*) FROM `progress` g
         WHERE g.daily_plan_id=p.daily_plan_id AND g.cycle_no=p.cycle_no
           AND g.round=1) AS cycle_size
FROM `progress` p
JOIN `word` w ON w.id=p.word_id
WHERE p.user_id=%s AND p.daily_plan_id=%s
  AND p.round BETWEEN %s AND %s AND p.status='pending'
ORDER BY p.cycle_no ASC, p.round ASC, p.id ASC
LIMIT 1
"""

# One due word across ALL date batches (Ebbinghaus review is word-level, not
# batch-level). review_round < 3 means the word is mid-review or not yet started
# this pass; ORDER BY review_round ASC yields the batch-by-sub-step flow
# (all "认识" first, then all "听写", then all "默写").
REVIEW_ITEM_SQL = """
SELECT p.id, p.review_stage, p.review_round, w.word, w.phonetic,
       w.translation, w.pos
FROM `progress` p
JOIN `word` w ON w.id = p.word_id
WHERE p.user_id=%s AND p.round=5 AND p.status='done'
  AND p.stage='memorized' AND p.next_due_at <= CURDATE()
  AND p.review_round < 3
ORDER BY p.review_round ASC, p.id ASC
LIMIT 1
"""


def _learn_plan(conn, uid):
    """Today's plan, preferring a still-in-progress ('learning') batch if one
    exists (defensive against legacy same-day duplicate batches)."""
    plan = one(
        conn,
        """SELECT * FROM `daily_plan`
           WHERE user_id=%s AND study_date=CURDATE() AND stage='learning'
           ORDER BY id DESC LIMIT 1""",
        (uid,),
    )
    if plan:
        return plan
    return one(
        conn,
        """SELECT * FROM `daily_plan`
           WHERE user_id=%s AND study_date=CURDATE()
           ORDER BY id DESC LIMIT 1""",
        (uid,),
    )


def fetch_state(conn, uid, mode, plan_id=None):
    """READ-ONLY state snapshot. Never writes — safe for GET /api/state."""
    if mode == "learn":
        plan = _learn_plan(conn, uid)
        if not plan:
            return {"kind": "need_plan", "pool": pool_size(conn, uid)}
        counts = plan_totals(conn, uid, plan["id"], 1, 5)
        row = one(conn, NEXT_ITEM_SQL, (uid, plan["id"], 1, 5))
        if not row:
            return {"kind": "learning_done", "plan_id": plan["id"],
                    "word_count": plan["word_count"],
                    "stage": plan["stage"] or "memorized"}
        payload = item_payload(row, plan, counts, f"r{row['round']}")
        # Word-level progress so the learner sees "第 3 / 10 词" — matching the
        # number they picked on the slider — instead of a round×5 tally.
        word_done = one(
            conn,
            """SELECT COUNT(*) AS c FROM (
                 SELECT word_id FROM `progress`
                 WHERE user_id=%s AND daily_plan_id=%s
                 GROUP BY word_id HAVING SUM(status='done') = COUNT(*)
               ) t""",
            (uid, plan["id"]),
        )["c"]
        payload["word_total"] = plan["word_count"]
        payload["word_done"] = int(word_done)
        return payload

    # Ebbinghaus review: next due word across all batches. No plan_id needed —
    # review is word-level (each word has its own due date), not batch-level.
    row = one(conn, REVIEW_ITEM_SQL, (uid,))
    if not row:
        return {"kind": "review_empty"}
    return review_item_payload(row)


def advance_state(conn, uid, mode, plan_id=None):
    """Advance plan/stage transitions after a WRITE. Call only from write routes.

    Learn mode: when all 5 rounds of today's plan are done, mark the batch
    "memorized" and seed the Ebbinghaus review queue (each word's round=5 row
    gets review_stage=0, review_round=0, next_due_at = today + first interval).
    Review mode needs no advance here — its progress is written inline in the
    answer route.
    """
    if mode == "learn":
        if plan_id:
            plan = one(conn,
                       "SELECT * FROM `daily_plan` WHERE id=%s AND user_id=%s",
                       (plan_id, uid))
        else:
            plan = _learn_plan(conn, uid)
        if not plan or plan["stage"] != "learning":
            return
        if plan_totals(conn, uid, plan["id"], 1, 5)["done"] < plan["word_count"] * 5:
            return
        execute(conn,
                """UPDATE `daily_plan`
                   SET stage='memorized', memorized_at=COALESCE(memorized_at, NOW())
                   WHERE id=%s AND stage='learning'""", (plan["id"],))
        execute(conn,
                "UPDATE `progress` SET stage='memorized' WHERE user_id=%s AND daily_plan_id=%s AND stage='none'",
                (uid, plan["id"]))
        execute(conn,
                """UPDATE `progress`
                   SET review_stage=0, review_round=0,
                       next_due_at=DATE_ADD(CURDATE(), INTERVAL %s DAY)
                   WHERE user_id=%s AND daily_plan_id=%s AND round=5""",
                (REVIEW_INTERVALS[0], uid, plan["id"]))
        return

    # review mode: nothing to advance here (handled in the answer route).


# ---------------------------------------------------------------- models

class PlanIn(BaseModel):
    count: int = Field(ge=1, le=200)


class AnswerIn(BaseModel):
    progress_id: int
    action: str
    value: str = ""
    assessment: str = ""


class KnownIn(BaseModel):
    progress_id: int


class HeartbeatIn(BaseModel):
    seconds: int = Field(ge=1, le=3600)


class RevealIn(BaseModel):
    article_id: int


class ExamAnswerIn(BaseModel):
    question_id: int
    week_key: str
    value: str = ""


class WordIn(BaseModel):
    word: str = Field(min_length=1, max_length=128)
    phonetic: str = Field(default="", max_length=128)
    translation: str = Field(default="", max_length=2000)
    pos: str = Field(default="", max_length=64)
    tag: str = Field(default="ky", max_length=128)


# ---------------------------------------------------------------- core endpoints

@app.get("/api/health")
def health():
    conn = connect()
    try:
        # Only expose the minimum needed by the UI. The DB account name used to
        # be returned here (CURRENT_USER()) and shown in the topbar; that is an
        # unnecessary information leak once the app is reachable off-machine.
        info = one(conn, "SELECT DATABASE() AS db, CURDATE() AS today")
        tables = one(conn,
                     "SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_schema=%s",
                     (DB_NAME,))
        words = one(conn, "SELECT COUNT(*) AS c FROM `word`")
        return {
            "ok": True,
            "database": info["db"],
            "tables": tables["c"],
            "word_count": words["c"],
            "server_date": str(info["today"]),
            "llm_configured": llm_configured(),
            "llm_model": current_model(),
            "local_mode": LOCAL_MODE,
        }
    finally:
        conn.close()


@app.get("/api/llm/models")
def llm_models(user=Depends(current_user)):
    """Server-defined model allowlist. No key or base_url is ever returned."""
    return {"configured": llm_configured(), "current": current_model(),
            "models": available_models()}


class ModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=64)


@app.post("/api/llm/model")
def llm_switch_model(req: ModelIn, user=Depends(require_admin)):
    """Switch the active model among presets; takes effect without restart.

    Admin-only: the model behind every learner's article/reading/chat generation
    is a system-wide setting, so only admins (or the trusted local self-use
    account) may change it. Regular learners still read it via /api/health.
    """
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再切换模型")
    try:
        active = switch_model(req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NoProvider as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "model": active}


@app.post("/api/llm/test")
def llm_test_model(user=Depends(require_admin)):
    """Live probe of the active model; reports busy/limit errors verbatim."""
    return test_model()


@app.get("/api/pool")
def pool(user=Depends(current_user)):
    with transaction() as conn:
        total = one(conn, "SELECT COUNT(*) AS c FROM `word`")["c"]
        return {"available": pool_size(conn, user["id"]), "total": total}


@app.get("/api/state")
def state(mode: str = "learn", plan_id: int | None = None,
          user=Depends(current_user)):
    if mode not in ("learn", "review"):
        raise HTTPException(status_code=400, detail="mode 只能是 learn 或 review")
    with transaction() as conn:
        return fetch_state(conn, user["id"], mode, plan_id)


@app.post("/api/plan")
def create_plan(req: PlanIn, user=Depends(current_user)):
    uid = user["id"]
    with transaction() as conn:
        existing = one(
            conn,
            "SELECT id FROM `daily_plan` WHERE user_id=%s AND study_date=CURDATE()",
            (uid,),
        )
        if existing:
            raise HTTPException(status_code=409, detail="今天已经建过学习计划了")
        available = pool_size(conn, uid)
        if req.count > available:
            raise HTTPException(
                status_code=400,
                detail=f"未掌握池只剩 {available} 个词，请减少 N 或联系管理员导入词库",
            )
        words = query(
            conn,
            """SELECT id FROM `word` w
               WHERE NOT EXISTS (
                 SELECT 1 FROM `progress` p WHERE p.user_id=%s AND p.word_id=w.id
               )
               ORDER BY w.id ASC LIMIT %s""",
            (uid, req.count),
        )
        cycle_count = (req.count + 4) // 5
        execute(
            conn,
            """INSERT INTO `daily_plan`
               (user_id, study_date, word_count, cycle_count, stage)
               VALUES (%s, CURDATE(), %s, %s, 'learning')""",
            (uid, req.count, cycle_count),
        )
        plan_id = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
        rows = [
            (uid, w["id"], plan_id, i // 5 + 1, r, "pending")
            for i, w in enumerate(words)
            for r in range(1, 6)
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO `progress`
                   (user_id, word_id, daily_plan_id, cycle_no, round, status)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                rows,
            )
        return {
            "plan_id": plan_id,
            "study_date": str(one(conn, "SELECT CURDATE() AS d")["d"]),
            "word_count": req.count,
            "cycle_count": cycle_count,
            "state": fetch_state(conn, uid, "learn"),
        }


def _answer_learn(conn, uid, p, req):
    """Handle a learning answer (rounds 1-5, status='pending')."""
    result = {
        "duplicate": False, "correct": None, "answer": None,
        "reveal": False, "standard_answer": None, "state": None,
    }
    r, action = p["round"], req.action
    if r == 1 and action == "unknown":
        execute(conn,
                "UPDATE `progress` SET wrong_count=wrong_count+1 WHERE id=%s AND status='pending'",
                (p["id"],))
        log_answer(conn, uid, p, "unknown")
        result["reveal"] = True
        return result

    if r == 1 and action == "known":
        require_mark_done(conn, p["id"])
        log_answer(conn, uid, p, "known", correct=1)
        result["correct"] = True

    elif r in (2, 3, 5) and action == "spell":
        if norm(req.value) == norm(p["spelling"]):
            require_mark_done(conn, p["id"])
            log_answer(conn, uid, p, "spell", value=req.value, correct=1)
            result["correct"] = True
        else:
            execute(conn,
                    "UPDATE `progress` SET wrong_count=wrong_count+1 WHERE id=%s AND status='pending'",
                    (p["id"],))
            log_answer(conn, uid, p, "spell", value=req.value, correct=0)
            result["correct"] = False
            result["answer"] = p["spelling"]
            return result

    elif r == 4 and action == "r4_reveal":
        log_answer(conn, uid, p, "r4_reveal", value=req.value)
        result["standard_answer"] = p["meaning"]
        return result

    elif r == 4 and action == "self_assess":
        if req.assessment not in ("remembered", "forgotten"):
            raise HTTPException(status_code=400, detail="请选择记住了或没记住")
        require_mark_done(conn, p["id"])
        log_answer(conn, uid, p, "self_assess", value=req.value,
                   assessment=req.assessment)
        result["correct"] = None

    else:
        raise HTTPException(status_code=400, detail="action 与当前轮次不匹配")

    advance_state(conn, uid, "learn", p["daily_plan_id"])
    result["state"] = fetch_state(conn, uid, "learn")
    return result


def _answer_review(conn, uid, p, req):
    """Handle an Ebbinghaus review answer (round=5, stage='memorized').

    Three sub-steps, driven by review_round:
      0 -> 认识 (r_known / r_unknown)
      1 -> 听写 (r_spell)
      2 -> 默写 (r_spell); a correct 默写 completes the pass.
    """
    result = {
        "duplicate": False, "correct": None, "answer": None,
        "reveal": False, "standard_answer": None, "state": None,
        "passed": False, "mastered": False,
    }
    rr = int(p["review_round"] or 0)
    action = req.action
    if rr == 0:
        if action == "r_known":
            execute(conn,
                    "UPDATE `progress` SET review_round=1, last_review_at=NOW() WHERE id=%s",
                    (p["id"],))
            log_answer(conn, uid, p, "review_known", correct=1)
            result["correct"] = True
        elif action == "r_unknown":
            review_fail(conn, p)
            log_answer(conn, uid, p, "review_unknown", correct=0)
            result["correct"] = False
            result["reveal"] = True
        else:
            raise HTTPException(status_code=400, detail="请选择认识或不认识")
    elif rr == 1:
        if action != "r_spell":
            raise HTTPException(status_code=400, detail="请默写这个单词")
        if norm(req.value) == norm(p["spelling"]):
            execute(conn,
                    "UPDATE `progress` SET review_round=2 WHERE id=%s", (p["id"],))
            log_answer(conn, uid, p, "review_spell", value=req.value, correct=1)
            result["correct"] = True
        else:
            review_fail(conn, p)
            log_answer(conn, uid, p, "review_spell", value=req.value, correct=0)
            result["correct"] = False
            result["answer"] = p["spelling"]
    elif rr == 2:
        if action != "r_spell":
            raise HTTPException(status_code=400, detail="请默写这个单词")
        if norm(req.value) == norm(p["spelling"]):
            review_pass(conn, p)
            log_answer(conn, uid, p, "review_spell", value=req.value, correct=1)
            result["correct"] = True
            result["passed"] = True
        else:
            review_fail(conn, p)
            log_answer(conn, uid, p, "review_spell", value=req.value, correct=0)
            result["correct"] = False
            result["answer"] = p["spelling"]
    else:
        raise HTTPException(status_code=400, detail="复习状态异常")
    result["state"] = fetch_state(conn, uid, "review")
    return result


@app.post("/api/answer")
def answer(req: AnswerIn, user=Depends(current_user)):
    uid = user["id"]
    with transaction() as conn:
        p = one(
            conn,
            """SELECT p.*, w.word AS spelling, w.translation AS meaning, w.pos
               FROM `progress` p JOIN `word` w ON w.id=p.word_id
               WHERE p.id=%s AND p.user_id=%s""",
            (req.progress_id, uid),
        )
        if not p:
            raise HTTPException(status_code=404, detail="找不到该题目")
        if p["status"] == "pending":
            # Still learning: rounds 1-5 not all finished yet.
            return _answer_learn(conn, uid, p, req)
        if p["round"] == 5 and p["stage"] == "memorized":
            # Finished learning -> now in the Ebbinghaus review queue.
            return _answer_review(conn, uid, p, req)
        # status='done' but neither a review word nor a pending learning word:
        # a duplicate submit of an already-completed learning step.
        result = {
            "duplicate": True, "correct": None, "answer": None,
            "reveal": False, "standard_answer": None, "state": None,
        }
        result["state"] = fetch_state(conn, uid, "learn")
        return result


@app.get("/api/review/stats")
def review_stats(user=Depends(current_user)):
    """Ebbinghaus review stats: due-now / in-pool / mastered counts."""
    uid = user["id"]
    with transaction() as conn:
        due = one(
            conn,
            """SELECT COUNT(*) AS c FROM `progress`
               WHERE user_id=%s AND round=5 AND status='done'
                 AND stage='memorized' AND next_due_at <= CURDATE()""",
            (uid,),
        )["c"]
        pool = one(
            conn,
            """SELECT COUNT(*) AS c FROM `progress`
               WHERE user_id=%s AND round=5 AND status='done' AND stage='memorized'""",
            (uid,),
        )["c"]
        mastered = one(
            conn,
            """SELECT COUNT(DISTINCT word_id) AS c FROM `progress`
               WHERE user_id=%s AND stage='mastered'""",
            (uid,),
        )["c"]
        return {"due": int(due), "pool": int(pool), "mastered": int(mastered)}


# ---------------------------------------------------------------- study dashboard

@app.post("/api/study/heartbeat")
def study_heartbeat(req: HeartbeatIn, user=Depends(current_user)):
    """Accumulate today's active study seconds. Idempotent per user+day."""
    with transaction() as conn:
        execute(
            conn,
            """INSERT INTO `study_time` (user_id, study_date, seconds)
               VALUES (%s, CURDATE(), %s)
               ON DUPLICATE KEY UPDATE seconds = seconds + VALUES(seconds)""",
            (user["id"], req.seconds),
        )
    return {"ok": True}


@app.get("/api/study/today")
def study_today(user=Depends(current_user)):
    """Today's study duration and count of words learned/backed so far."""
    uid = user["id"]
    with transaction() as conn:
        seconds = one(
            conn,
            "SELECT seconds FROM `study_time` WHERE user_id=%s AND study_date=CURDATE()",
            (uid,),
        )
        learned = one(
            conn,
            """SELECT COUNT(DISTINCT word_id) AS c FROM `progress`
               WHERE user_id=%s AND stage IN ('memorized','mastered')
                 AND DATE(updated_at) = CURDATE()""",
            (uid,),
        )
    return {
        "seconds": int(seconds["seconds"]) if seconds else 0,
        "learned_words": int(learned["c"]),
    }


@app.post("/api/word/known")
def mark_known(req: KnownIn, user=Depends(current_user)):
    """「熟知」button: mark a word as already known — skip learning and review.

    A fully "known"-skipped plan must still unlock its daily articles, so we
    run advance_state here too (a word that is already mastered counts as done).
    """
    uid = user["id"]
    with transaction() as conn:
        p = one(conn, "SELECT word_id, daily_plan_id FROM `progress` WHERE id=%s AND user_id=%s",
                (req.progress_id, uid))
        if not p:
            raise HTTPException(status_code=404, detail="找不到该单词")
        execute(conn,
                "UPDATE `progress` SET status='done', stage='mastered' WHERE user_id=%s AND word_id=%s",
                (uid, p["word_id"]))
        execute(conn,
                "UPDATE `progress` SET review_round=0, review_stage=0, next_due_at=NULL WHERE user_id=%s AND word_id=%s",
                (uid, p["word_id"]))
        advance_state(conn, uid, "learn", p["daily_plan_id"])
        return {"ok": True}


# ---------------------------------------------------------------- daily articles

@app.get("/api/articles/batches")
def article_batches(user=Depends(current_user)):
    with transaction() as conn:
        return {"batches": daily_batches(conn, user["id"])}


@app.get("/api/articles/scenes")
def article_scenes():
    return {"scenes": [{"id": key, "label": SCENE_LABELS.get(key, key)}
                       for key in SCENES]}


@app.get("/api/articles/daily")
def articles_daily(plan_id: int, scene: str = "default", user=Depends(current_user)):
    with transaction() as conn:
        articles, err = ensure_daily_articles(conn, user["id"], plan_id, scene)
        if err:
            raise HTTPException(status_code=400, detail=err)
        payloads = [article_payload(conn, a) for a in articles]
        mode = payloads[0]["mode"]
        total = payloads[0]["total_words"]
        covered = sum(p["covered_words"] for p in payloads)
        if mode == "full":
            summary = f"本批次 {total} 词已全部收入 {len(payloads)} 篇短文。"
        else:
            summary = (f"本批次词量较大，已从 {total} 词中抽取 {covered} 词编入 "
                       f"{len(payloads)} 篇短文，其余 {total - covered} 词见下方未收录清单，"
                       "可在复习板块单独复习。")
        return {"plan_id": plan_id, "mode": mode, "summary": summary,
                "scene": payloads[0]["scene"], "articles": payloads}


@app.post("/api/articles/reveal")
def reveal_article(req: RevealIn, user=Depends(current_user)):
    with transaction() as conn:
        art = one(
            conn,
            """SELECT * FROM `article` WHERE id=%s AND user_id=%s""",
            (req.article_id, user["id"]),
        )
        if not art:
            raise HTTPException(status_code=404, detail="找不到这篇短文")
        # Revealing is a per-view action (not persisted). Return a clickable
        # layer for EVERY word: target words carry their saved snapshot and
        # is_target=True; every other word is looked up in the built-in
        # dictionary with is_target=False. Works for daily and weekly alike.
        return {"ok": True, "segments": article_full_segments(conn, art)}


@app.post("/api/articles/regenerate")
def regenerate_daily_article(req: RevealIn, user=Depends(current_user)):
    """Re-write one saved daily article using the current LLM configuration."""
    try:
        with transaction() as conn:
            title, provider = regenerate_article(
                conn, user_id=user["id"], article_id=req.article_id)
    except GenerationFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到这篇短文或对应的批次词")
    return {"ok": True, "generator": provider, "title": title}


# ---------------------------------------------------------------- weekly reading

@app.get("/api/reading/weeks")
def reading_weeks(user=Depends(current_user)):
    with transaction() as conn:
        rows = query(
            conn,
            """SELECT DISTINCT week_key FROM `article`
               WHERE user_id=%s AND kind='weekly' ORDER BY week_key DESC""",
            (user["id"],),
        )
        keys = [r["week_key"] for r in rows]
        current = week_key_of(date.today())
        keys = [current] + [k for k in keys if k != current]
        return {"weeks": keys[:12], "current": current}


@app.post("/api/reading/publish-now")
def publish_now(user=Depends(current_user)):
    """Preview / trial button: generate this week's two passages before Friday 18:00.

    Generates only the caller's own week; the Friday-evening scheduler remains
    the default for every account. Idempotent - never replaces existing articles.
    """
    key = week_key_of(date.today())
    with transaction() as conn:
        result = ensure_weekly(conn, user["id"], key, force=True)
        return {"ok": True, "week_key": key, "status": result["status"],
                "article_count": len(result["articles"])}


@app.get("/api/reading")
def reading(week_key: str = "", user=Depends(current_user)):
    key = week_key or week_key_of(date.today())
    if not re.fullmatch(r"\d{4}-W\d{2}", key):
        raise HTTPException(status_code=400, detail="周次格式应为 2026-W45")
    with transaction() as conn:
        return reading_payload(conn, user["id"], key)


@app.post("/api/reading/answer")
def submit_exam(req: ExamAnswerIn, user=Depends(current_user)):
    uid = user["id"]
    with transaction() as conn:
        q = one(
            conn,
            """SELECT q.* FROM `question` q WHERE q.id=%s AND q.user_id=%s""",
            (req.question_id, uid),
        )
        if not q:
            raise HTTPException(status_code=404, detail="找不到这道题")
        art = one(
            conn,
            """SELECT * FROM `article` WHERE id=%s AND user_id=%s AND kind='weekly'""",
            (q["article_id"], uid),
        )
        if not art or art["week_key"] != req.week_key:
            raise HTTPException(status_code=400, detail="题目与周次不匹配")
        late = datetime.now() > art["deadline"]
        result = grade_answer(conn, uid, q, req.value, req.week_key, late)
        result["late"] = late
        return result


# ---------------------------------------------------------------- admin (word bank only)

@app.get("/api/admin/stats")
def admin_stats(user=Depends(require_admin)):
    with transaction() as conn:
        sources = query(
            conn,
            """SELECT COALESCE(source,'unknown') AS source, COUNT(*) AS c
               FROM `word` GROUP BY source ORDER BY c DESC""",
        )
        return {
            "users": one(conn, "SELECT COUNT(*) AS c FROM `user` WHERE password_hash<>''")["c"],
            "plans": one(conn, "SELECT COUNT(*) AS c FROM `daily_plan`")["c"],
            "articles": one(conn, "SELECT COUNT(*) AS c FROM `article`")["c"],
            "sources": sources,
        }


@app.get("/api/admin/users")
def admin_users(user=Depends(require_admin)):
    """Admin-only: every registered learner with a study summary. This is the
    concrete privilege that separates the admin console from a learner's view —
    no learner endpoint ever lists other accounts or their progress."""
    with transaction() as conn:
        rows = query(
            conn,
            """SELECT u.id, u.username, u.created_at,
                      (SELECT COUNT(*) FROM `daily_plan` dp
                        WHERE dp.user_id=u.id) AS plans,
                      (SELECT COUNT(DISTINCT p.word_id) FROM `progress` p
                        WHERE p.user_id=u.id AND p.stage='mastered') AS mastered_words,
                      (SELECT COUNT(*) FROM `progress` p
                        WHERE p.user_id=u.id AND p.status='done') AS done_items,
                      (SELECT COUNT(*) FROM `chat_session` c
                        WHERE c.user_id=u.id) AS chat_sessions
               FROM `user` u
               WHERE u.password_hash<>''
               ORDER BY u.id""",
        )
        for r in rows:
            r["created_at"] = str(r["created_at"])
        return {"users": rows}


@app.get("/api/admin/words")
def admin_words(q: str = "", limit: int = 20, user=Depends(require_admin)):
    limit = max(1, min(100, limit))
    with transaction() as conn:
        if q:
            rows = query(
                conn,
                """SELECT id, word, phonetic, translation, pos, tag, source, verified
                   FROM `word` WHERE word LIKE %s OR translation LIKE %s
                   ORDER BY id ASC LIMIT %s""",
                (f"%{q}%", f"%{q}%", limit),
            )
        else:
            rows = query(
                conn,
                """SELECT id, word, phonetic, translation, pos, tag, source, verified
                   FROM `word` ORDER BY id DESC LIMIT %s""",
                (limit,),
            )
        return {"words": rows, "total": one(conn, "SELECT COUNT(*) AS c FROM `word`")["c"]}


@app.post("/api/admin/word")
def admin_upsert_word(req: WordIn, user=Depends(require_admin)):
    word = req.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="单词不能为空")
    with transaction() as conn:
        execute(
            conn,
            """INSERT INTO `word`
               (`word`,`phonetic`,`translation`,`pos`,`tag`,`source`,`verified`)
               VALUES (%s,%s,%s,%s,%s,'admin',1)
               ON DUPLICATE KEY UPDATE
                 phonetic=VALUES(phonetic), translation=VALUES(translation),
                 pos=VALUES(pos), tag=VALUES(tag), source='admin', verified=1""",
            (word, req.phonetic.strip() or None, req.translation.strip() or None,
             req.pos.strip() or None, req.tag.strip() or "ky"),
        )
        return {"ok": True, "word": word}


# ---------------------------------------------------------------- AI tutor chat (side channel)

class ChatStartIn(BaseModel):
    source_type: str = "general"
    source_id: int | None = None
    first_question: str = ""


class ChatAskIn(BaseModel):
    session_id: int
    question: str


class ChatSessionIn(BaseModel):
    session_id: int


class ChatPracticeIn(BaseModel):
    kind: str  # cloze | reading
    session_id: int | None = None
    source_type: str = "general"
    source_id: int | None = None


@app.post("/api/chat/practice")
def chat_practice(req: ChatPracticeIn, user=Depends(current_user)):
    """Generate a structured, interactive practice card (cloze / reading).

    The card is stored as a marked assistant message in the chat session, so it
    stays in context for follow-up questions and is still a strict side channel:
    nothing here writes progress / answer_log / the five-round state.
    """
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    if req.kind not in ("cloze", "reading"):
        raise HTTPException(status_code=400, detail="不支持的题型")
    if req.source_type not in ("article", "question", "general"):
        raise HTTPException(status_code=400, detail="对话来源不合法")
    with transaction() as conn:
        if req.session_id:
            session = chat_service.get_session(conn, req.session_id, user["id"])
            if not session:
                raise HTTPException(status_code=404, detail="找不到这次对话")
            sid = req.session_id
        else:
            context = chat_service.build_context(conn, req.source_type,
                                                 req.source_id, user["id"])
            if req.source_type in ("article", "question") and context is None:
                raise HTTPException(status_code=404, detail="找不到这篇内容或无权访问")
            sid = chat_service.start_session(conn, user["id"], req.source_type,
                                             req.source_id, context)
            session = chat_service.get_session(conn, sid, user["id"])
        label = ("选词填空" if req.kind == "cloze" else "阅读理解")
        intent = f"出一篇考研风格{label}题"
        try:
            card = chat_service.generate_practice_card(conn, user["id"], req.kind)
            result = chat_service.send_card(conn, session, user["id"], intent, card)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except (GenerationFailed, NoProvider) as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "session_id": sid, "title": session["title"], **result}


@app.get("/api/chat/sessions")
def chat_sessions(user=Depends(current_user)):
    with transaction() as conn:
        rows = chat_service.list_sessions(conn, user["id"])
    return {"sessions": [{"id": r["id"], "title": r["title"],
                          "source_type": r["source_type"],
                          "source_id": r["source_id"],
                          "turn_count": r["turn_count"],
                          "updated_at": str(r["updated_at"])} for r in rows]}


@app.post("/api/chat/start")
def chat_start(req: ChatStartIn, user=Depends(current_user)):
    """Open a tutor session, optionally frozen to one article or question."""
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    if req.source_type not in ("article", "question", "general"):
        raise HTTPException(status_code=400, detail="对话来源不合法")
    with transaction() as conn:
        context = chat_service.build_context(conn, req.source_type, req.source_id,
                                             user["id"])
        if req.source_type in ("article", "question") and context is None:
            raise HTTPException(status_code=404, detail="找不到这篇内容或无权访问")
        sid = chat_service.start_session(conn, user["id"], req.source_type,
                                         req.source_id, context)
        first = None
        if req.first_question.strip():
            session = chat_service.get_session(conn, sid, user["id"])
            try:
                first = chat_service.reply(conn, session, user["id"],
                                           req.first_question)
            except (GenerationFailed, NoProvider) as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
    payload = {"ok": True, "session_id": sid,
               "title": (context.get("title") or context["label"]) if context else "通用英语"}
    if first:
        payload["reply"] = first
    return payload


@app.get("/api/chat/history")
def chat_history(session_id: int, user=Depends(current_user)):
    with transaction() as conn:
        messages = chat_service.history(conn, session_id, user["id"])
    if messages is None:
        raise HTTPException(status_code=404, detail="找不到这次对话")
    return {"session_id": session_id, "messages": messages}


@app.post("/api/chat/ask")
def chat_ask(req: ChatAskIn, user=Depends(current_user)):
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    with transaction() as conn:
        session = chat_service.get_session(conn, req.session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="找不到这次对话")
        try:
            result = chat_service.reply(conn, session, user["id"], req.question)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except (GenerationFailed, NoProvider) as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, **result}


@app.post("/api/chat/delete")
def chat_delete(req: ChatSessionIn, user=Depends(current_user)):
    with transaction() as conn:
        if not chat_service.delete_session(conn, req.session_id, user["id"]):
            raise HTTPException(status_code=404, detail="找不到这次对话")
    return {"ok": True}


# ---------------------------------------------------------------- static frontend

if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
else:
    @app.get("/")
    def no_frontend():
        return HTMLResponse(
            "<meta charset='utf-8'><h3>前端还没构建</h3>"
            "<p>请先双击运行项目根目录的 setup.bat，或进入 frontend 目录执行 "
            "<code>npm install</code> 与 <code>npm run build</code>。</p>",
            status_code=200,
        )
