"""学习面板：今日时长、今日词数、熟知跳过。

从 main.py 拆出的路由模块，路径前缀不变。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user
from db import execute, one, transaction
from study_state import advance_state


router = APIRouter()


class HeartbeatIn(BaseModel):
    seconds: int = Field(ge=1, le=3600)


class KnownIn(BaseModel):
    progress_id: int


@router.post("/api/study/heartbeat")
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


@router.get("/api/study/today")
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
               WHERE user_id=%s AND book_id=%s
                 AND stage IN ('memorized','mastered')
                 AND DATE(updated_at) = CURDATE()""",
            (uid, user["book_id"]),
        )
    return {
        "seconds": int(seconds["seconds"]) if seconds else 0,
        "learned_words": int(learned["c"]),
    }


@router.post("/api/word/known")
def mark_known(req: KnownIn, user=Depends(current_user)):
    """「熟知」button: mark a word as already known — skip learning and review.

    A fully "known"-skipped plan must still unlock its daily articles, so we
    run advance_state here too (a word that is already mastered counts as done).
    """
    uid = user["id"]
    with transaction() as conn:
        p = one(
            conn,
            """SELECT word_id, daily_plan_id, book_id FROM `progress`
               WHERE id=%s AND user_id=%s""",
            (req.progress_id, uid),
        )
        if not p:
            raise HTTPException(status_code=404, detail="找不到该单词")
        execute(conn,
                """UPDATE `progress` SET status='done', stage='mastered'
                   WHERE user_id=%s AND book_id=%s AND word_id=%s""",
                (uid, p["book_id"], p["word_id"]))
        execute(conn,
                """UPDATE `progress`
                   SET review_round=0, review_stage=0, next_due_at=NULL
                   WHERE user_id=%s AND book_id=%s AND word_id=%s""",
                (uid, p["book_id"], p["word_id"]))
        advance_state(conn, uid, "learn", p["book_id"], p["daily_plan_id"])
        return {"ok": True}
