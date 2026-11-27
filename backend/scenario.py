"""Scenario-based vocabulary practice: micro-sentences (A) and visual scenes (C).

Both are a STRICT side channel: they never write progress / answer_log / the
five-round state. Rows are generated lazily, cached per (word, book), and never
overwritten, so a generated sentence or scene stays stable across refreshes.
"""

import base64
import html
import re
import textwrap

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
PALETTES = [
    ("#eef2ff", "#c7d2fe", "#4f46e5"),
    ("#ecfdf5", "#a7f3d0", "#059669"),
    ("#fff7ed", "#fed7aa", "#ea580c"),
    ("#fdf2f8", "#fbcfe8", "#db2777"),
    ("#f0f9ff", "#bae6fd", "#0284c7"),
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


def _scene_valid(data):
    if not isinstance(data, dict):
        return False
    text = data.get("scene_text")
    return isinstance(text, str) and 8 <= len(text) <= 300


def _llm_scene(row, book):
    exam = (book or {}).get("exam_type") or "考研英语"
    prompt = (
        f"You describe ONE vivid visual scene to help a {exam} learner remember "
        f"a word. Word: {row['word']}: {row['translation'] or ''}. "
        'Return ONLY JSON: {"scene_text":"一句中文场景描述，20-50字，画面感强，'
        '不要出现英文单词","image_prompt":"English prompt for generating the scene"}. '
        "No markdown, no extra keys."
    )
    data, provider = generate_json(prompt, validator=_scene_valid, max_tokens=220)
    return (data["scene_text"].strip(),
            str(data.get("image_prompt") or "").strip()[:300], provider)


def _local_scene(row):
    meaning = (row["translation"] or "").split(";")[0].strip() or row["word"]
    text = f"一张关于「{meaning}」的课堂挂图：教室的墙上贴着相关照片，学生指着它讨论用法。"
    return text[:300], f"illustration of {row['word']}, clean flat style", "local"


def svg_scene_card(scene_text, seed_text):
    """Deterministic, dependency-free scene image (SVG data URI)."""
    bg1, bg2, accent = PALETTES[sum(ord(c) for c in seed_text) % len(PALETTES)]
    lines = textwrap.wrap(scene_text or "", 16)[:4]
    y0 = 186 - (len(lines) - 1) * 19
    body = "".join(
        f'<text x="240" y="{y0 + i * 38}" text-anchor="middle" '
        f'font-size="20" font-family="sans-serif" fill="#1f2937">{html.escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" '
        'viewBox="0 0 480 300">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/>'
        '</linearGradient></defs>'
        '<rect width="480" height="300" rx="24" fill="url(#g)"/>'
        f'<circle cx="110" cy="96" r="46" fill="{accent}" opacity="0.28"/>'
        f'<rect x="330" y="54" width="86" height="86" rx="18" fill="{accent}" opacity="0.20"/>'
        f'<circle cx="392" cy="232" r="34" fill="{accent}" opacity="0.24"/>'
        f'<circle cx="76" cy="236" r="26" fill="{accent}" opacity="0.18"/>'
        + body + '</svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def ensure_scene(conn, word_id, book_id, book=None):
    """Return a cached visual scene card, generating once on first request."""
    cached = one(
        conn,
        """SELECT scene_text, image_prompt, image_url, source FROM `word_scene`
           WHERE word_id=%s AND book_id=%s""",
        (word_id, book_id),
    )
    if cached:
        out = dict(cached)
        out["image_url"] = out["image_url"] or svg_scene_card(out["scene_text"], out["scene_text"])
        return out
    row = _word_row(conn, word_id)
    if not row:
        return None
    text, image_prompt, provider = _local_scene(row)
    if configured():
        try:
            detach_for_llm(conn)
            text, image_prompt, provider = _llm_scene(row, book)
        except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
            text, image_prompt, provider = _local_scene(row)
    image_url = svg_scene_card(text, row["word"])
    execute(
        conn,
        """INSERT IGNORE INTO `word_scene`
           (word_id, book_id, scene_text, image_prompt, image_url, source)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (word_id, book_id, text, image_prompt, image_url, provider),
    )
    return {"scene_text": text, "image_prompt": image_prompt,
            "image_url": image_url, "source": provider}
