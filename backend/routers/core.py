"""核心学员接口：健康检查、模型切换、书架、未学池、学习/复习状态机。

从 main.py 拆出的第一个路由模块。路径前缀不变，前端无需改动。
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import LOCAL_MODE, current_user, require_admin
from db import DB_NAME, execute, one, query
from db import pool as db_pool
from db import transaction
from llm import (
    NoProvider, available_models, configured as llm_configured,
    current_model, switch_model, test_model,
)
from study_state import (
    advance_state, book_pattern, book_row, fetch_state, log_answer, norm,
    pool_size, require_mark_done, review_fail, review_pass,
)

router = APIRouter()

# /api/health is polled by the UI; its two COUNT(*) queries are the only slow
# part, so they are cached for this long. Mutated only by health().
HEALTH_META_TTL = 30.0
_HEALTH_META = {"at": 0.0, "word_count": 0, "tables": 0}


@router.get("/api/health")
def health():
    # word_count / table count change only on an admin import, so they are
    # cached: information_schema scans are the most expensive thing on this
    # endpoint. DATABASE()/CURDATE() still hit the DB so a dead MySQL is still
    # reported honestly.
    global _HEALTH_META
    with transaction() as conn:
        info = one(conn, "SELECT DATABASE() AS db, CURDATE() AS today")
        fresh = (time.time() - _HEALTH_META["at"]) < HEALTH_META_TTL
        if not fresh:
            words = one(conn, "SELECT COUNT(*) AS c FROM `word`")
            tables = one(conn,
                         "SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_schema=%s",
                         (DB_NAME,))
            _HEALTH_META = {"at": time.time(), "word_count": int(words["c"]),
                            "tables": int(tables["c"])}
    return {
        "ok": True,
        "database": info["db"],
        "tables": _HEALTH_META["tables"],
        "word_count": _HEALTH_META["word_count"],
        "server_date": str(info["today"]),
        "llm_configured": llm_configured(),
        "llm_model": current_model(),
        "local_mode": LOCAL_MODE,
        "pool": db_pool.stats(),
    }


@router.get("/api/llm/models")
def llm_models(user=Depends(current_user)):
    """Server-defined model allowlist. No key or base_url is ever returned."""
    return {"configured": llm_configured(), "current": current_model(),
            "models": available_models()}


class ModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=64)


@router.post("/api/llm/model")
def llm_switch_model(req: ModelIn, user=Depends(require_admin)):
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再切换模型")
    try:
        active = switch_model(req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NoProvider as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "model": active}


@router.post("/api/llm/test")
def llm_test_model(user=Depends(require_admin)):
    """Live probe of the active model; reports busy/limit errors verbatim."""
    return test_model()


@router.get("/api/books")
def list_books(user=Depends(current_user)):
    """The four shelves with per-book word/mastery counts for this user."""
    with transaction() as conn:
        books = query(
            conn,
            """SELECT id, code, name, exam_type, tag_filter, description
               FROM `book` WHERE enabled=1 ORDER BY sort_no, id""",
        )
        for b in books:
            b["word_count"] = one(
                conn,
                """SELECT COUNT(*) AS c FROM `word`
                   WHERE CONCAT(' ', IFNULL(`tag`,''), ' ') LIKE %s""",
                (book_pattern(b),),
            )["c"]
            b["learned"] = one(
                conn,
                """SELECT COUNT(DISTINCT word_id) AS c FROM `progress`
                   WHERE user_id=%s AND book_id=%s""",
                (user["id"], b["id"]),
            )["c"]
            b["current"] = (b["id"] == user["book_id"])
        return {"books": books, "current_book_id": user["book_id"]}


@router.get("/api/book/current")
def get_current_book(user=Depends(current_user)):
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        return {"book": {k: book[k] for k in
                         ("id", "code", "name", "exam_type", "tag_filter", "description")}}


class BookIn(BaseModel):
    book_id: int


@router.post("/api/book/current")
def switch_book(req: BookIn, user=Depends(current_user)):
    """Persist the chosen shelf on the user row; affects all later requests."""
    with transaction() as conn:
        book = one(conn, "SELECT * FROM `book` WHERE id=%s AND enabled=1", (req.book_id,))
        if not book:
            raise HTTPException(status_code=404, detail="找不到这本书")
        changed = execute(conn,
                          "UPDATE `user` SET current_book_id=%s WHERE id=%s",
                          (book["id"], user["id"]))
        if not changed:
            # Admin identities live in the `admin` table, not `user`.
            raise HTTPException(status_code=400, detail="当前账号不支持切换书籍")
        return {"ok": True, "book_id": book["id"], "book_name": book["name"]}


@router.get("/api/pool")
def pool(user=Depends(current_user)):
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        total = one(
            conn,
            """SELECT COUNT(*) AS c FROM `word`
               WHERE CONCAT(' ', IFNULL(`tag`,''), ' ') LIKE %s""",
            (book_pattern(book),),
        )["c"]
        return {"available": pool_size(conn, user["id"], book), "total": total,
                "book_id": book["id"], "book_name": book["name"]}


@router.get("/api/state")
def state(mode: str = "learn", plan_id: int | None = None,
          user=Depends(current_user)):
    if mode not in ("learn", "review"):
        raise HTTPException(status_code=400, detail="mode 只能是 learn 或 review")
    with transaction() as conn:
        return fetch_state(conn, user["id"], mode, user["book_id"], plan_id)


class PlanIn(BaseModel):
    count: int = Field(ge=1, le=200)


@router.post("/api/plan")
def create_plan(req: PlanIn, user=Depends(current_user)):
    uid = user["id"]
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        existing = one(
            conn,
            """SELECT id FROM `daily_plan`
               WHERE user_id=%s AND book_id=%s AND study_date=CURDATE()""",
            (uid, book["id"]),
        )
        if existing:
            raise HTTPException(status_code=409,
                                detail=f"今天在「{book['name']}」已经建过学习计划了")
        available = pool_size(conn, uid, book)
        if req.count > available:
            raise HTTPException(
                status_code=400,
                detail=f"未掌握池只剩 {available} 个词，请减少 N 或联系管理员导入词库",
            )
        words = query(
            conn,
            """SELECT id FROM `word` w
               WHERE CONCAT(' ', IFNULL(w.tag,''), ' ') LIKE %s
                 AND NOT EXISTS (
                   SELECT 1 FROM `progress` p
                   WHERE p.user_id=%s AND p.book_id=%s AND p.word_id=w.id
                 )
               ORDER BY w.id ASC LIMIT %s""",
            (book_pattern(book), uid, book["id"], req.count),
        )
        cycle_count = (req.count + 4) // 5
        execute(
            conn,
            """INSERT INTO `daily_plan`
               (user_id, book_id, study_date, word_count, cycle_count, stage)
               VALUES (%s, %s, CURDATE(), %s, %s, 'learning')""",
            (uid, book["id"], req.count, cycle_count),
        )
        plan_id = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
        rows = [
            (uid, book["id"], w["id"], plan_id, i // 5 + 1, r, "pending")
            for i, w in enumerate(words)
            for r in range(1, 6)
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO `progress`
                   (user_id, book_id, word_id, daily_plan_id, cycle_no, round, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                rows,
            )
        return {
            "plan_id": plan_id,
            "study_date": str(one(conn, "SELECT CURDATE() AS d")["d"]),
            "word_count": req.count,
            "cycle_count": cycle_count,
            "book_id": book["id"],
            "book_name": book["name"],
            "state": fetch_state(conn, uid, "learn", book["id"]),
        }


class AnswerIn(BaseModel):
    progress_id: int
    action: str
    value: str = ""
    assessment: str = ""


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

    advance_state(conn, uid, "learn", p["book_id"], p["daily_plan_id"])
    result["state"] = fetch_state(conn, uid, "learn", p["book_id"])
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
    result["state"] = fetch_state(conn, uid, "review", p["book_id"])
    return result


@router.post("/api/answer")
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
        result["state"] = fetch_state(conn, uid, "learn", p["book_id"])
        return result


@router.get("/api/review/stats")
def review_stats(user=Depends(current_user)):
    """Ebbinghaus review stats: due-now / in-pool / mastered counts."""
    uid = user["id"]
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        due = one(
            conn,
            """SELECT COUNT(*) AS c FROM `progress`
               WHERE user_id=%s AND book_id=%s AND round=5 AND status='done'
                 AND stage='memorized' AND next_due_at <= CURDATE()""",
            (uid, book["id"]),
        )["c"]
        pool = one(
            conn,
            """SELECT COUNT(*) AS c FROM `progress`
               WHERE user_id=%s AND book_id=%s AND round=5 AND status='done'
                 AND stage='memorized'""",
            (uid, book["id"]),
        )["c"]
        mastered = one(
            conn,
            """SELECT COUNT(DISTINCT word_id) AS c FROM `progress`
               WHERE user_id=%s AND book_id=%s AND stage='mastered'""",
            (uid, book["id"]),
        )["c"]
        return {"due": int(due), "pool": int(pool), "mastered": int(mastered),
                "book_id": book["id"], "book_name": book["name"]}
