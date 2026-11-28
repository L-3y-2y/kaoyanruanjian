"""管理端接口：词库维护、学员清单、首页聚合指标。

从 main.py 拆出的路由模块，路径前缀不变。所有接口都要求管理员身份，
且所有指标排除 shadow=1 的管理员影子学员行。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_admin
from db import execute, one, query, transaction

router = APIRouter()


class WordIn(BaseModel):
    word: str = Field(min_length=1, max_length=128)
    phonetic: str = Field(default="", max_length=128)
    translation: str = Field(default="", max_length=2000)
    pos: str = Field(default="", max_length=64)
    tag: str = Field(default="ky", max_length=128)


@router.get("/api/admin/stats")
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


@router.get("/api/admin/users")
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


@router.get("/api/admin/words")
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


@router.post("/api/admin/word")
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


@router.get("/api/admin/overview")
def admin_overview(days: int = 14, user=Depends(require_admin)):
    """管理端首页需要的全部数字，一次请求取齐，避免前端并发打五个接口。

    刻意只聚合「指导动作」的指标：今天有没有人来、来了做了多少、复习有没有积压、
    语境填空错在哪些词。不做没有对应动作的虚荣指标。
    """
    days = max(7, min(30, int(days)))
    today = date.today()
    with transaction() as conn:
        head = one(
            conn,
            """SELECT COUNT(*) AS items, COUNT(DISTINCT user_id) AS users
               FROM `answer_log` WHERE DATE(created_at)=CURDATE()
                 AND user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)""",
        )
        due = one(
            conn,
            """SELECT COUNT(*) AS c FROM `progress`
               WHERE status='done' AND stage<>'mastered' AND next_due_at<=CURDATE()
                 AND user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)""",
        )
        cloze = one(
            conn,
            """SELECT COUNT(*) AS total, COALESCE(SUM(correct=1),0) AS ok
               FROM `answer_log`
               WHERE kind='cloze' AND created_at>=DATE_SUB(NOW(), INTERVAL 14 DAY)
                 AND user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)""",
        )
        learners = one(
            conn, "SELECT COUNT(*) AS c FROM `user` WHERE password_hash<>''"
        )
        last = one(
            conn,
            """SELECT MAX(created_at) AS t, COUNT(DISTINCT user_id) AS c
               FROM `answer_log` WHERE created_at>=DATE_SUB(CURDATE(), INTERVAL 6 DAY)
                 AND user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)""",
        )
        rows = query(
            conn,
            """SELECT DATE(created_at) AS d, COUNT(*) AS items,
                      COUNT(DISTINCT user_id) AS users
               FROM `answer_log`
               WHERE created_at>=DATE_SUB(CURDATE(), INTERVAL %s DAY)
                 AND user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)
               GROUP BY DATE(created_at) ORDER BY d""",
            (days - 1,),
        )
        hot = query(
            conn,
            """SELECT w.word, w.translation, COUNT(*) AS errors
               FROM `answer_log` a JOIN `word` w ON w.id=a.word_id
               WHERE a.kind='cloze' AND a.correct=0
                 AND a.created_at>=DATE_SUB(NOW(), INTERVAL 14 DAY)
                 AND a.user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)
               GROUP BY w.id, w.word, w.translation
               ORDER BY errors DESC, w.word ASC LIMIT 8""",
        )
        sources = query(
            conn,
            """SELECT COALESCE(source,'unknown') AS source, COUNT(*) AS c
               FROM `word` GROUP BY source ORDER BY c DESC LIMIT 6""",
        )
        books = query(
            conn,
            """SELECT b.id, b.name, COUNT(DISTINCT p.user_id) AS users
               FROM `book` b LEFT JOIN `progress` p ON p.book_id=b.id
                 AND p.user_id NOT IN (SELECT id FROM `user` WHERE shadow=1)
               GROUP BY b.id, b.name ORDER BY b.sort_no""",
        )

    # 补齐没有答题的日期，否则前端柱状图会把「没人学」的空白挤掉。
    series = {}
    for r in rows:
        key = r["d"] if isinstance(r["d"], str) else str(r["d"])
        series[key[:10]] = {"date": key[:10], "items": int(r["items"]),
                            "users": int(r["users"])}
    daily = []
    for i in range(days - 1, -1, -1):
        d = today.fromordinal(today.toordinal() - i)
        key = d.isoformat()
        daily.append(series.get(key, {"date": key, "items": 0, "users": 0}))
    for r in daily:
        r["weekend"] = date.fromisoformat(r["date"]).weekday() >= 5

    total = int(cloze["total"] or 0)
    ok = int(cloze["ok"] or 0)
    return {
        "kpi": {
            "today_users": int(head["users"] or 0),
            "today_items": int(head["items"] or 0),
            "due_backlog": int(due["c"] or 0),
            "cloze_rate": round(ok * 100.0 / total, 1) if total else None,
            "cloze_total": total,
            "learners": int(learners["c"] or 0),
            "week_active": int(last["c"] or 0),
            "last_answer_at": str(last["t"]) if last["t"] else None,
        },
        "daily": daily,
        "hot": hot,
        "sources": sources,
        "books": books,
    }


@router.get("/api/admin/learners")
def admin_learners(user=Depends(require_admin)):
    """跨账号学员表：当天做了多少、积压多少、语境错多少、最后活跃时间。

    「状态」由最近活跃时间推导，前端按 <1 天 / 2-3 天 / ≥4 天 分稳定、波动、掉队。
    """
    with transaction() as conn:
        rows = query(
            conn,
            """SELECT u.id, u.username, u.created_at, COALESCE(b.name,'—') AS book,
                      (SELECT COUNT(DISTINCT p.word_id) FROM `progress` p
                        WHERE p.user_id=u.id AND p.stage='mastered') AS mastered,
                      (SELECT COUNT(*) FROM `progress` p
                        WHERE p.user_id=u.id AND p.status='done'
                          AND p.stage<>'mastered' AND p.next_due_at<=CURDATE()) AS due,
                      (SELECT COUNT(*) FROM `answer_log` a
                        WHERE a.user_id=u.id AND a.kind='cloze' AND a.correct=0) AS cloze_errors,
                      (SELECT COUNT(*) FROM `answer_log` a
                        WHERE a.user_id=u.id AND DATE(a.created_at)=CURDATE()) AS today_items,
                      (SELECT COUNT(*) FROM `progress` p
                        WHERE p.user_id=u.id AND p.status='done'
                          AND DATE(p.updated_at)=CURDATE()) AS today_done,
                      (SELECT MAX(a.created_at) FROM `answer_log` a
                        WHERE a.user_id=u.id) AS last_at
               FROM `user` u
               LEFT JOIN `book` b ON b.id=u.current_book_id
               WHERE u.password_hash<>''
               ORDER BY last_at DESC""",
        )
    today = date.today()
    out = []
    for r in rows:
        last = r["last_at"]
        days_idle = None if last is None else (today - last.date()).days
        r["last_at"] = str(last) if last else None
        r["days_idle"] = days_idle
        if days_idle is None:
            state = "never"
        elif days_idle <= 1:
            state = "stable"
        elif days_idle <= 3:
            state = "wobble"
        else:
            state = "lost"
        r["state"] = state
        r["created_at"] = str(r["created_at"])
        for k in ("mastered", "due", "cloze_errors", "today_items", "today_done"):
            r[k] = int(r[k] or 0)
        out.append(r)
    order = {"stable": 0, "wobble": 1, "lost": 2, "never": 3}
    out.sort(key=lambda x: (order[x["state"]], -(x["days_idle"] or 999)))
    summary = {
        "total": len(out),
        "stable": sum(1 for r in out if r["state"] == "stable"),
        "wobble": sum(1 for r in out if r["state"] == "wobble"),
        "lost": sum(1 for r in out if r["state"] in ("lost", "never")),
    }
    return {"learners": out, "summary": summary}
