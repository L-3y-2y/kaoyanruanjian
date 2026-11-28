"""Scenario-based vocabulary practice: micro-sentences (A) and visual scenes (C).

Both are a STRICT side channel: they never write progress / answer_log / the
five-round state. Rows are generated lazily, cached per (word, book), and never
overwritten, so a generated sentence or scene stays stable across refreshes.
"""

import base64
import re

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


def _scene_valid(data, row=None):
    if not isinstance(data, dict):
        return False
    text = data.get("scene_text")
    if not (isinstance(text, str) and 8 <= len(text) <= 300):
        return False
    if row:
        # The scene must NOT give the answer away.
        low = text.lower()
        if str(row.get("word") or "").lower() in low:
            return False
        for part in re.split(r"[;,，；/、\s]+", str(row.get("translation") or "")):
            if len(part.strip()) >= 2 and part.strip() in text:
                return False
    return True


def _llm_scene(row, book):
    exam = (book or {}).get("exam_type") or "考研英语"
    prompt = (
        f"You describe ONE vivid visual scene to help a {exam} learner remember "
        f"a word. Word: {row['word']}: {row['translation'] or ''}. "
        'Return ONLY JSON: {"scene_text":"一句中文场景描述，20-50字，画面感强，'
        '只描述画面里能看到的物体、人物和环境；严禁出现该英文单词、它的中文释义或近义词",'
        '"image_prompt":"English prompt for generating the scene"}. '
        "No markdown, no extra keys."
    )
    data, provider = generate_json(prompt,
                                   validator=lambda d: _scene_valid(d, row),
                                   max_tokens=220)
    return (data["scene_text"].strip(),
            str(data.get("image_prompt") or "").strip()[:300], provider)


def _local_scene(row):
    # Keep the fallback generic: the answer is masked before it reaches the UI,
    # but a neutral scene is a better clue than a caption naming the word.
    text = "教室里，学生把书摊开在桌上，阳光落在纸页上，旁边的笔记写了一半。"
    return text[:300], f"illustration of {row['word']}, clean flat style", "local"


def mask_answer(text, word, translation):
    """Hide the target word and its Chinese senses from a scene description."""
    out = str(text or "")
    w = str(word or "").strip()
    if w:
        out = re.sub(r"\b" + re.escape(w) + r"\w*\b", "______", out, flags=re.I)
    for part in re.split(r"[;,，；/、\s]+", str(translation or "")):
        part = part.strip()
        if len(part) >= 2 and part in out:
            out = out.replace(part, "______")
    return out


_SUN_RAYS = "".join(
    f'<line x1="30" y1="3" x2="30" y2="11" stroke="#f59e0b" stroke-width="3" '
    f'stroke-linecap="round" transform="rotate({a} 30 30)"/>'
    for a in range(0, 360, 45)
)
_MOTIFS = {
    "sun": '<circle cx="30" cy="30" r="14" fill="#f59e0b"/>' + _SUN_RAYS,
    "tree": ('<rect x="26" y="36" width="8" height="20" fill="#92400e"/>'
             '<circle cx="30" cy="24" r="17" fill="#10b981"/>'),
    "book": ('<path d="M6 16 Q30 8 54 16 L54 50 Q30 42 6 50 Z" fill="#4f46e5"/>'
             '<path d="M30 13 L30 46" stroke="#ffffff" stroke-width="2"/>'),
    "person": ('<circle cx="30" cy="16" r="10" fill="#f59e0b"/>'
               '<path d="M10 56 Q30 28 50 56 Z" fill="#3b82f6"/>'),
    "water": ('<path d="M4 28 Q17 18 30 28 T56 28 L56 50 L4 50 Z" fill="#0ea5e9"/>'
              '<path d="M4 40 Q17 30 30 40 T56 40" stroke="#ffffff" stroke-width="2" fill="none"/>'),
    "moon": '<path d="M38 10 A22 22 0 1 0 38 50 A18 18 0 1 1 38 10 Z" fill="#6366f1"/>',
    "fire": ('<path d="M30 6 Q48 26 42 42 Q36 56 30 56 Q18 56 18 40 Q18 24 30 6 Z" fill="#f97316"/>'),
    "apple": ('<circle cx="30" cy="34" r="18" fill="#ef4444"/>'
              '<path d="M30 16 Q34 8 42 10" stroke="#15803d" stroke-width="4" fill="none"/>'),
    "bulb": ('<circle cx="30" cy="26" r="15" fill="#facc15"/>'
             '<rect x="24" y="42" width="12" height="12" rx="2" fill="#9ca3af"/>'),
}
_MOTIF_RULES = (
    (("阳光", "太阳", "sun", "light", "灯"), "sun"),
    (("树", "树叶", "森林", "tree", "forest"), "tree"),
    (("书", "阅读", "笔记", "知识", "book", "read"), "book"),
    (("学生", "同学", "老师", "人", "student", "people"), "person"),
    (("水", "海", "河", "雨", "water", "sea"), "water"),
    (("夜", "月亮", "night", "moon"), "moon"),
    (("火", "燃烧", "fire"), "fire"),
    (("吃", "食物", "苹果", "food", "apple"), "apple"),
)


def visual_scene_card(scene_text, word="", seed_text=""):
    """Deterministic picture-style SVG card; never prints the answer on the image."""
    seed = seed_text or word or scene_text or "?"
    bg1, bg2, accent = PALETTES[sum(ord(c) for c in seed) % len(PALETTES)]
    picked = []
    for keys, name in _MOTIF_RULES:
        if name not in picked and any(k in str(scene_text or "") for k in keys):
            picked.append(name)
    picked = (picked or ["book"])[:3]
    while len(picked) < 3:
        picked.append("bulb")
    icons = "".join(
        f'<g transform="translate({x} 88) scale(1.25)">{_MOTIFS[name]}</g>'
        for x, name in zip((78, 208, 338), picked)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" '
        'viewBox="0 0 480 300">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/>'
        '</linearGradient></defs>'
        '<rect width="480" height="300" rx="24" fill="url(#g)"/>'
        f'<circle cx="70" cy="62" r="40" fill="{accent}" opacity="0.16"/>'
        f'<rect x="382" y="188" width="72" height="72" rx="20" fill="{accent}" opacity="0.14"/>'
        + icons +
        '<text x="240" y="262" text-anchor="middle" font-size="19" '
        'font-family="sans-serif" fill="#475569">看图猜词</text>'
        '</svg>'
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
    row = _word_row(conn, word_id)
    if not row:
        return None
    if cached:
        out = dict(cached)
        # The stored data URI may be truncated by an older VARCHAR(1024)
        # column. Rebuild it on every read so cached scenes always render.
        out["image_url"] = visual_scene_card(out.get("scene_text"), row["word"])
        out["hint_text"] = mask_answer(out.get("scene_text"), row["word"],
                                       row.get("translation"))
        return out
    text, image_prompt, provider = _local_scene(row)
    if configured():
        try:
            detach_for_llm(conn)
            text, image_prompt, provider = _llm_scene(row, book)
        except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
            text, image_prompt, provider = _local_scene(row)
    # Do not persist the data URI: VARCHAR(1024) silently truncates it.
    # The card is derived deterministically from scene_text on every read.
    execute(
        conn,
        """INSERT IGNORE INTO `word_scene`
           (word_id, book_id, scene_text, image_prompt, image_url, source)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (word_id, book_id, text, image_prompt, "", provider),
    )
    return {"scene_text": text, "image_prompt": image_prompt,
            "image_url": visual_scene_card(text, row["word"]),
            "hint_text": mask_answer(text, row["word"], row.get("translation")),
            "source": provider}
