"""学习状态机：五轮学习 + 艾宾浩斯复习的纯数据库操作。

从 main.py 抽出来，供 routers/core.py、routers/study.py、routers/articles.py
共用。这里只有「给定的连接上做什么」，不创建 app、不注册路由。

Rules that must never be broken (see docs/design-decisions.md):
  - Progress lives ONLY in MySQL. The frontend never keeps a cursor or index.
  - The next item is found by querying pending rows ordered by cycle_no -> round -> id.
  - Progress update and answer_log insertion share ONE transaction.
  - Every user-data endpoint resolves its identity from the session cookie,
    never from anything the browser sends as a user id.
"""

import re

from fastapi import HTTPException

from db import execute, one

REVIEW_INTERVALS = (1, 2, 4, 7, 15, 30)


def norm(value):
    """Case / whitespace normalisation for spelling answers."""
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def book_row(conn, book_id):
    """Current book row, with a safe fallback for stale/deleted ids."""
    row = one(conn, "SELECT * FROM `book` WHERE id=%s AND enabled=1", (book_id,))
    return row or one(conn, "SELECT * FROM `book` WHERE id=1")


def book_pattern(book):
    """Space-padded LIKE pattern so `ky` never matches inside another tag."""
    return "% " + (book or {}).get("tag_filter", "ky") + " %"


def pool_size(conn, uid, book):
    """Unstudied words inside the CURRENT book only (progress is per-book)."""
    return one(
        conn,
        """SELECT COUNT(*) AS c FROM `word` w
           WHERE CONCAT(' ', IFNULL(w.tag,''), ' ') LIKE %s
             AND NOT EXISTS (
               SELECT 1 FROM `progress` p
               WHERE p.user_id=%s AND p.book_id=%s AND p.word_id=w.id
             )""",
        (book_pattern(book), uid, book["id"]),
    )["c"]


def log_answer(conn, uid, p, action, value=None, correct=None, assessment=None):
    execute(
        conn,
        """INSERT INTO `answer_log`
           (user_id, book_id, word_id, daily_plan_id, progress_id, round, kind,
            action, user_answer, correct, self_assessment)
           VALUES (%s,%s,%s,%s,%s,%s,'word',%s,%s,%s,%s)""",
        (uid, p["book_id"], p["word_id"], p["daily_plan_id"], p["id"], p["round"],
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
                   cloze_wrong=0, next_due_at=NULL, last_review_at=NOW()
               WHERE id=%s""",
            (new_stage, p["id"]),
        )
        # Mirror the mastered flag onto every round row of this word.
        execute(
            conn,
            """UPDATE `progress` SET stage='mastered', cloze_wrong=0
               WHERE user_id=%s AND book_id=%s AND word_id=%s""",
            (p["user_id"], p["book_id"], p["word_id"]),
        )
    else:
        execute(
            conn,
            """UPDATE `progress`
               SET review_round=0, review_stage=%s, cloze_wrong=0,
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
WHERE p.user_id=%s AND p.book_id=%s AND p.daily_plan_id=%s
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
WHERE p.user_id=%s AND p.book_id=%s AND p.round=5 AND p.status='done'
  AND p.stage='memorized' AND p.next_due_at <= CURDATE()
  AND p.review_round < 3
ORDER BY p.cloze_wrong DESC, p.review_round ASC, p.id ASC
LIMIT 1
"""


def _learn_plan(conn, uid, book_id):
    """Today's plan FOR THE CURRENT BOOK, preferring an in-progress batch."""
    plan = one(
        conn,
        """SELECT * FROM `daily_plan`
           WHERE user_id=%s AND book_id=%s AND study_date=CURDATE()
             AND stage='learning'
           ORDER BY id DESC LIMIT 1""",
        (uid, book_id),
    )
    if plan:
        return plan
    return one(
        conn,
        """SELECT * FROM `daily_plan`
           WHERE user_id=%s AND book_id=%s AND study_date=CURDATE()
           ORDER BY id DESC LIMIT 1""",
        (uid, book_id),
    )


def fetch_state(conn, uid, mode, book_id, plan_id=None):
    """READ-ONLY state snapshot. Never writes — safe for GET /api/state."""
    book = book_row(conn, book_id)
    if mode == "learn":
        plan = _learn_plan(conn, uid, book["id"])
        if not plan:
            return {"kind": "need_plan", "pool": pool_size(conn, uid, book)}
        counts = plan_totals(conn, uid, plan["id"], 1, 5)
        row = one(conn, NEXT_ITEM_SQL, (uid, book["id"], plan["id"], 1, 5))
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
                 WHERE user_id=%s AND book_id=%s AND daily_plan_id=%s
                 GROUP BY word_id HAVING SUM(status='done') = COUNT(*)
               ) t""",
            (uid, book["id"], plan["id"]),
        )["c"]
        payload["word_total"] = plan["word_count"]
        payload["word_done"] = int(word_done)
        payload["book_id"] = book["id"]
        return payload

    # Ebbinghaus review: next due word across all batches OF THIS BOOK.
    row = one(conn, REVIEW_ITEM_SQL, (uid, book["id"]))
    if not row:
        return {"kind": "review_empty"}
    return review_item_payload(row)


def _finalize_learn(conn, uid, plan, book_id):
    """All five rounds done: enter the Ebbinghaus review pool immediately."""
    if not plan or plan["stage"] != "learning":
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
               WHERE user_id=%s AND daily_plan_id=%s AND round=5 AND stage='memorized'""",
            (REVIEW_INTERVALS[0], uid, plan["id"]))


def advance_state(conn, uid, mode, book_id, plan_id=None):
    """Advance plan/stage transitions after a WRITE. Call only from write routes.

    Learn mode: once all 5 rounds are done, finalize straight into the
    Ebbinghaus review pool. Context practice lives in the article page and
    never gates progress.
    """
    if mode == "learn":
        if plan_id:
            plan = one(conn,
                       """SELECT * FROM `daily_plan`
                          WHERE id=%s AND user_id=%s AND book_id=%s""",
                       (plan_id, uid, book_id))
        else:
            plan = _learn_plan(conn, uid, book_id)
        if not plan or plan["stage"] != "learning":
            return
        if plan_totals(conn, uid, plan["id"], 1, 5)["done"] < plan["word_count"] * 5:
            return
        _finalize_learn(conn, uid, plan, book_id)
        return

    # review mode: nothing to advance here (handled in the answer route).
