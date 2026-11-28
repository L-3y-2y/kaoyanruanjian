"""文章相关接口：批次短文、语境填空、单词点击层、单篇重生成。

从 main.py 拆出的路由模块，路径前缀不变。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from content import (
    SCENES, SCENE_LABELS, article_full_segments, article_payload,
    daily_batches, ensure_daily_articles, regenerate_article,
)
from db import execute, one, query, transaction
from llm import GenerationFailed
from study_state import book_row, norm
import scenario as scenario_service

router = APIRouter()


class QuizCheckIn(BaseModel):
    item_id: int
    value: str = ""


class RevealIn(BaseModel):
    article_id: int


def _past_sentences(conn, book_id, word_ids):
    """Sentences already generated for these words: never show them twice."""
    if not word_ids:
        return {}
    marks = ",".join(["%s"] * len(word_ids))
    rows = query(
        conn,
        f"""SELECT word_id, sentence FROM `word_example`
            WHERE book_id=%s AND word_id IN ({marks})
            ORDER BY id DESC""",
        (book_id, *word_ids),
    )
    out = {}
    for r in rows:
        out.setdefault(r["word_id"], []).append(r["sentence"])
    return out


def _quiz_candidates(conn, user, book, art):
    """Words to quiz on, plus the sentences each new sentence must differ from."""
    marked = query(
        conn,
        """SELECT m.word_id, m.surface, m.translation_snapshot AS translation,
                  w.word, w.pos, w.translation AS base_translation
           FROM `article_word` m JOIN `word` w ON w.id=m.word_id
           WHERE m.article_id=%s ORDER BY m.position ASC LIMIT 5""",
        (art["id"],),
    )
    extra = []
    plan_id = art.get("daily_plan_id")
    if plan_id:
        extra = query(
            conn,
            """SELECT DISTINCT w.id AS word_id, w.word, w.pos,
                      w.translation AS base_translation
               FROM `progress` p JOIN `word` w ON w.id=p.word_id
               WHERE p.user_id=%s AND p.book_id=%s AND p.daily_plan_id=%s
                 AND p.round=5 AND w.id NOT IN (
                     SELECT word_id FROM `article_word` WHERE article_id=%s)
               ORDER BY w.id LIMIT 5""",
            (user["id"], book["id"], plan_id, art["id"]),
        )
    ids = [r["word_id"] for r in marked] + [r["word_id"] for r in extra]
    past = _past_sentences(conn, book["id"], ids)

    def cands(rows, kind):
        out = []
        for r in rows:
            word = r["word"]
            avoid = [scenario_service.mask_sentence(s, word)
                     for s in past.get(r["word_id"], [])[:6]]
            # Never reuse the sentence the user just read in the article.
            in_article = scenario_service.sentence_with(
                art["body"], r.get("surface") or word)
            if in_article:
                avoid.append(scenario_service.mask_sentence(in_article, word))
            out.append({
                "word_id": r["word_id"], "word": word, "pos": r.get("pos") or "",
                "translation": (r.get("translation")
                                or r.get("base_translation") or ""),
                "kind": kind, "avoid": avoid,
            })
        return out

    return cands(marked, "article") + cands(extra, "extra")


def _save_quiz_items(conn, user, art, items):
    """Persist masked sentences (answers stay server-side) and the used-sentence log."""
    for it in items:
        execute(
            conn,
            """INSERT IGNORE INTO `article_cloze`
               (user_id, book_id, article_id, word_id, sentence, translation,
                answer, source)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user["id"], user["book_id"], art["id"], it["word_id"],
             it["sentence"], it["translation"], it["answer"], it["source"]),
        )
        nxt = one(
            conn,
            """SELECT IFNULL(MAX(seq),0)+1 AS n FROM `word_example`
               WHERE word_id=%s AND book_id=%s""",
            (it["word_id"], user["book_id"]),
        )["n"]
        execute(
            conn,
            """INSERT IGNORE INTO `word_example`
               (word_id, book_id, seq, sentence, translation, source)
               VALUES (%s,%s,%s,%s,%s,'quiz')""",
            (it["word_id"], user["book_id"], nxt, it["raw"], it["translation"]),
        )


def _quiz_payload(conn, user, article_id):
    rows = query(
        conn,
        """SELECT id, sentence, status, attempts FROM `article_cloze`
           WHERE article_id=%s AND user_id=%s AND book_id=%s
           ORDER BY source='extra', id""",
        (article_id, user["id"], user["book_id"]),
    )
    items = [{
        "id": r["id"], "sentence": r["sentence"],
        "status": r["status"], "attempts": int(r["attempts"] or 0),
    } for r in rows]
    passed = sum(1 for r in rows if r["status"] == "passed")
    return {"items": items, "done": passed, "total": len(rows),
            "complete": bool(rows) and passed == len(rows)}


@router.get("/api/articles/cloze")
def article_cloze(article_id: int, user=Depends(current_user)):
    """Fill-in quiz: every sentence is freshly written, never the article's own.

    Reading the plan and writing the rows happen in separate short transactions
    so the (parallel) LLM calls never hold a database transaction open.
    """
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        art = one(
            conn,
            """SELECT * FROM `article`
               WHERE id=%s AND user_id=%s AND book_id=%s""",
            (article_id, user["id"], book["id"]),
        )
        if not art:
            raise HTTPException(status_code=404, detail="找不到这篇短文")
        ready = one(
            conn,
            """SELECT COUNT(*) AS c FROM `article_cloze`
               WHERE article_id=%s AND user_id=%s""",
            (art["id"], user["id"]),
        )["c"]
        candidates = [] if ready else _quiz_candidates(conn, user, book, art)

    if candidates:
        items = scenario_service.build_items(candidates, book)
        with transaction() as conn:
            _save_quiz_items(conn, user, art, items)

    with transaction() as conn:
        return _quiz_payload(conn, user, art["id"])


@router.post("/api/articles/cloze/check")
def article_cloze_check(req: QuizCheckIn, user=Depends(current_user)):
    """Grade one fill-in. Wrong answers flag the word for next-day review."""
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        row = one(
            conn,
            """SELECT c.*, w.word AS answer FROM `article_cloze` c
               JOIN `word` w ON w.id=c.word_id
               WHERE c.id=%s AND c.user_id=%s AND c.book_id=%s""",
            (req.item_id, user["id"], book["id"]),
        )
        if not row:
            raise HTTPException(status_code=404, detail="找不到这道语境填空题")
        if row["status"] == "passed":
            return {"correct": True, "answer": row["answer"],
                    "done": None, "total": None, "weak": False}
        given = norm(req.value)
        # Base form or the inflected form actually used in the sentence both count.
        correct = bool(given) and given in set(
            scenario_service.word_forms(row["answer"]))
        execute(conn,
                """UPDATE `article_cloze`
                   SET attempts=attempts+1, status=IF(%s,'passed','pending')
                   WHERE id=%s""", (int(correct), row["id"]))
        # Every wrong attempt marks the word weak; review queue prioritises it.
        if not correct:
            execute(conn,
                    """UPDATE `progress` SET cloze_wrong=1
                       WHERE user_id=%s AND book_id=%s AND word_id=%s AND round=5""",
                    (user["id"], book["id"], row["word_id"]))
        execute(conn,
                """INSERT INTO `answer_log`
                   (user_id, book_id, word_id, round, kind, action,
                    user_answer, correct)
                   VALUES (%s,%s,%s,5,'cloze','article_cloze',%s,%s)""",
                (user["id"], book["id"], row["word_id"], req.value, int(correct)))
        totals = one(
            conn,
            """SELECT COUNT(*) AS total, SUM(status='passed') AS passed
               FROM `article_cloze` WHERE article_id=%s""",
            (row["article_id"],),
        )
        return {"correct": correct, "answer": row["answer"] if correct else None,
                "done": int(totals["passed"] or 0), "total": int(totals["total"] or 0),
                "weak": not correct}


@router.get("/api/articles/batches")
def article_batches(user=Depends(current_user)):
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        return {"batches": daily_batches(conn, user["id"], book["id"]),
                "book_id": book["id"], "book_name": book["name"]}


@router.get("/api/articles/scenes")
def article_scenes():
    return {"scenes": [{"id": key, "label": SCENE_LABELS.get(key, key)}
                       for key in SCENES]}


@router.get("/api/articles/daily")
def articles_daily(plan_id: int, scene: str = "default", user=Depends(current_user)):
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        articles, err = ensure_daily_articles(
            conn, user["id"], plan_id, scene, book["id"], book)
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


@router.post("/api/articles/reveal")
def reveal_article(req: RevealIn, user=Depends(current_user)):
    with transaction() as conn:
        art = one(
            conn,
            """SELECT * FROM `article`
               WHERE id=%s AND user_id=%s AND book_id=%s""",
            (req.article_id, user["id"], user["book_id"]),
        )
        if not art:
            raise HTTPException(status_code=404, detail="找不到这篇短文")
        # Revealing is a per-view action (not persisted). Return a clickable
        # layer for EVERY word: target words carry their saved snapshot and
        # is_target=True; every other word is looked up in the built-in
        # dictionary with is_target=False. Works for daily and weekly alike.
        return {"ok": True, "segments": article_full_segments(conn, art)}


@router.post("/api/articles/regenerate")
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
