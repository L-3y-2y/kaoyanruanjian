"""周阅读：周次列表、提前发布、正文、客观题作答。

从 main.py 拆出的路由模块，路径前缀不变。
"""

import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from content import ensure_weekly, grade_answer, reading_payload, week_key_of
from db import one, query, transaction
from study_state import book_row

router = APIRouter()


class ExamAnswerIn(BaseModel):
    question_id: int
    week_key: str
    value: str = ""


@router.get("/api/reading/weeks")
def reading_weeks(user=Depends(current_user)):
    with transaction() as conn:
        rows = query(
            conn,
            """SELECT DISTINCT week_key FROM `article`
               WHERE user_id=%s AND book_id=%s AND kind='weekly'
               ORDER BY week_key DESC""",
            (user["id"], user["book_id"]),
        )
        keys = [r["week_key"] for r in rows]
        current = week_key_of(date.today())
        keys = [current] + [k for k in keys if k != current]
        return {"weeks": keys[:12], "current": current}


@router.post("/api/reading/publish-now")
def publish_now(user=Depends(current_user)):
    """Preview / trial button: generate this week's two passages before Friday 18:00.

    Generates only the caller's own week; the Friday-evening scheduler remains
    the default for every account. Idempotent - never replaces existing articles.
    """
    key = week_key_of(date.today())
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        result = ensure_weekly(conn, user["id"], key, force=True,
                               book_id=book["id"], book=book)
        return {"ok": True, "week_key": key, "status": result["status"],
                "book_id": book["id"], "article_count": len(result["articles"])}


@router.get("/api/reading")
def reading(week_key: str = "", user=Depends(current_user)):
    key = week_key or week_key_of(date.today())
    if not re.fullmatch(r"\d{4}-W\d{2}", key):
        raise HTTPException(status_code=400, detail="周次格式应为 2026-W45")
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        return reading_payload(conn, user["id"], key, book["id"], book)


@router.post("/api/reading/answer")
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
