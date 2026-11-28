"""Fresh example sentences for the article context quiz.

A sentence is generated once per (word, book) and cached in `word_example`.
Used to cover today's words that the daily article did not include. Pure
practice content: it never writes progress or gates the learning flow.
"""

from db import detach_for_llm, execute, one, query
from llm import GenerationFailed, NoProvider, configured, generate_json

LOCAL_SENTENCES = {
    "n": [
        "On the desk lay a {w} she had marked the night before.",
        "The reading room offered every {w} a student might need.",
        "She wrote {w} in the margin and underlined it twice.",
    ],
    "v": [
        "She would {w} again whenever the first attempt failed.",
        "Small groups {w} together long after the lecture ended.",
        "It took a while to {w}, but the effort paid off by evening.",
    ],
    "adj": [
        "The example felt {w}, yet it clarified the whole paragraph.",
        "A {w} detail can change the meaning of an entire sentence.",
        "The answer seemed {w} at first, and only later became obvious.",
    ],
}
LOCAL_GENERIC = [
    "That afternoon {w} came up again in a completely different text.",
    "In the margin she added a note about {w} and moved on.",
    "A classmate mentioned {w}, so she looked it up once more.",
]


def scenario_words(conn, user_id, book_id, limit=10):
    """Today's words of the current book, with sensible fallbacks."""
    rows = query(
        conn,
        """SELECT DISTINCT w.id, w.word, w.translation, w.pos
           FROM `progress` p JOIN `word` w ON w.id=p.word_id
           WHERE p.user_id=%s AND p.book_id=%s
             AND p.daily_plan_id IN (
               SELECT id FROM `daily_plan`
               WHERE user_id=%s AND book_id=%s AND study_date=CURDATE())
           ORDER BY w.id ASC LIMIT %s""",
        (user_id, book_id, user_id, book_id, limit),
    )
    if not rows:
        rows = query(
            conn,
            """SELECT DISTINCT w.id, w.word, w.translation, w.pos
               FROM `progress` p JOIN `word` w ON w.id=p.word_id
               WHERE p.user_id=%s AND p.book_id=%s AND p.stage IN ('memorized','mastered')
               ORDER BY w.id ASC LIMIT %s""",
            (user_id, book_id, limit),
        )
    if not rows:
        tag = one(conn, "SELECT tag_filter FROM `book` WHERE id=%s", (book_id,))
        pattern = "% " + (tag["tag_filter"] if tag else "ky") + " %"
        rows = query(
            conn,
            """SELECT id, word, translation, pos FROM `word`
               WHERE CONCAT(' ', IFNULL(`tag`,''), ' ') LIKE %s
               ORDER BY id ASC LIMIT %s""",
            (pattern, limit),
        )
    return [{"id": r["id"], "word": r["word"],
             "translation": (r["translation"] or "").split(";")[0].strip(),
             "pos": r["pos"] or ""} for r in rows]


def _word_row(conn, word_id):
    return one(conn, "SELECT id, word, translation, pos FROM `word` WHERE id=%s",
               (word_id,))


def _example_valid(data, word):
    if not isinstance(data, dict):
        return False
    sentence = data.get("sentence")
    return (isinstance(sentence, str) and 8 <= len(sentence.split()) <= 40
            and word.lower() in sentence.lower())


def _llm_example(row, book):
    exam = (book or {}).get("exam_type") or "考研英语"
    prompt = (
        f"You write ONE short English example sentence for a Chinese student "
        f"preparing for {exam}. Word: {row['word']} [{row['pos'] or 'word'}]: "
        f"{row['translation'] or ''}. "
        'Return ONLY JSON: {"sentence":"...","translation":"中文翻译"}. '
        "12-25 words, natural exam-style context, use the word in a natural "
        "form. No markdown, no extra keys."
    )
    data, provider = generate_json(prompt, validator=lambda d: _example_valid(d, row["word"]),
                                   max_tokens=220)
    return (data["sentence"].strip(),
            str(data.get("translation") or "").strip()[:300], provider)


def _local_example(row):
    pos = (row["pos"] or "").split(".")[0].split("/")[0]
    templates = LOCAL_SENTENCES.get(pos, LOCAL_GENERIC)
    sentence = templates[sum(ord(c) for c in row["word"]) % len(templates)].format(w=row["word"])
    return sentence, (row["translation"] or "").split(";")[0].strip()[:300], "local"


def ensure_example(conn, word_id, book_id, book=None):
    """Return a cached micro-sentence, generating once on first request."""
    cached = one(
        conn,
        """SELECT seq, sentence, translation, source FROM `word_example`
           WHERE word_id=%s AND book_id=%s ORDER BY seq ASC""",
        (word_id, book_id),
    )
    if cached:
        return dict(cached)
    row = _word_row(conn, word_id)
    if not row:
        return None
    sentence, translation, provider = _local_example(row)
    if configured():
        try:
            detach_for_llm(conn)
            sentence, translation, provider = _llm_example(row, book)
        except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
            sentence, translation, provider = _local_example(row)
    execute(
        conn,
        """INSERT IGNORE INTO `word_example`
           (word_id, book_id, seq, sentence, translation, source)
           VALUES (%s,%s,1,%s,%s,%s)""",
        (word_id, book_id, sentence, translation, provider),
    )
    return {"seq": 1, "sentence": sentence, "translation": translation,
            "source": provider}
