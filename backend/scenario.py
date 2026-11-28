"""Scenario-based vocabulary practice: micro-sentences (A) and visual scenes (C).

Both are a STRICT side channel: they never write progress / answer_log / the
five-round state. Rows are generated lazily, cached per (word, book), and never
overwritten, so a generated sentence or scene stays stable across refreshes.
"""

import base64
import random
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


def _cloud(x, y, s=1.0, opacity=0.85):
    return (
        f'<g transform="translate({x} {y}) scale({s})" fill="#ffffff" opacity="{opacity}">'
        '<ellipse cx="0" cy="0" rx="34" ry="13"/>'
        '<circle cx="-18" cy="-6" r="14"/><circle cx="2" cy="-12" r="18"/>'
        '<circle cx="22" cy="-5" r="13"/></g>'
    )


def _tree(x, y, s=1.0, tone="#16a34a"):
    return (
        f'<g transform="translate({x} {y}) scale({s})">'
        '<rect x="-5" y="0" width="10" height="48" rx="4" fill="#92400e"/>'
        f'<circle cx="0" cy="-8" r="26" fill="{tone}"/>'
        f'<circle cx="-18" cy="4" r="18" fill="{tone}" opacity=".9"/>'
        f'<circle cx="18" cy="4" r="18" fill="{tone}" opacity=".9"/></g>'
    )


def _sun(x, y, r=18, color="#fbbf24"):
    rays = "".join(
        f'<line x1="{x}" y1="{y - r - 9}" x2="{x}" y2="{y - r - 2}" '
        f'stroke="{color}" stroke-width="3" stroke-linecap="round" '
        f'transform="rotate({a} {x} {y})"/>'
        for a in range(0, 360, 45)
    )
    return (
        f'<circle cx="{x}" cy="{y}" r="{r + 14}" fill="{color}" opacity=".18"/>'
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>' + rays
    )


def _scene_theme(text):
    t = str(text or "")
    if any(k in t for k in ("夜", "月亮", "星光", "moon", "night")):
        return "night"
    if any(k in t for k in ("海", "河", "湖", "水", "雨", "波", "water", "sea", "river", "rain")):
        return "water"
    if any(k in t for k in ("书", "阅读", "笔记", "纸", "学生", "教室", "学习", "知识",
                            "考试", "book", "read", "study", "exam")):
        return "study"
    if any(k in t for k in ("阳光", "太阳", "树", "森林", "山", "花", "鸟", "路",
                            "sun", "tree", "forest", "park")):
        return "outdoor"
    return "abstract"


def _scene_study(rng):
    """A student reading by the window, with a warm desk in the foreground."""
    clouds = "".join(_cloud(x, y, s, .7) for x, y, s in
                     ((330, 52, .35), (420, 90, .28)))
    inner = f"""
    <rect width="480" height="224" fill="url(#wall)" opacity=".65"/>
    <rect x="290" y="22" width="168" height="142" rx="11" fill="#ffffff" opacity=".9"/>
    <rect x="299" y="31" width="150" height="124" rx="6" fill="url(#sky)"/>
    <g clip-path="url(#win)">{_sun(408, 67, 15)}{clouds}
      <path d="M299 132 Q330 104 360 132 Q394 106 449 132 L449 155 L299 155 Z"
            fill="#86efac" opacity=".85"/>
      {_tree(326, 155, .55, "#4ade80")}{_tree(424, 155, .42, "#22c55e")}
    </g>
    <path d="M299 93 L449 93 M374 31 L374 155" stroke="#ffffff" stroke-width="5"/>
    <path d="M299 155 L449 155 L330 300 L96 300 Z" fill="url(#beam)" opacity=".42"/>
    <path d="M112 222 Q116 172 168 172 Q220 172 224 222 Z" fill="#60a5fa"/>
    <path d="M148 176 L168 198 L188 176" fill="#ffffff" opacity=".85"/>
    <rect x="156" y="152" width="24" height="24" rx="10" fill="#fcd9b6"/>
    <circle cx="168" cy="133" r="27" fill="#fcd9b6"/>
    <path d="M141 132 Q141 101 168 101 Q195 101 195 132 Q184 114 168 114 Q150 114 141 132 Z"
          fill="#3f3f46"/>
    <circle cx="158" cy="134" r="2" fill="#3f3f46"/><circle cx="179" cy="134" r="2" fill="#3f3f46"/>
    <rect x="0" y="222" width="480" height="78" fill="url(#desk)"/>
    <rect x="0" y="222" width="480" height="5" fill="#fbbf24" opacity=".45"/>
    <g filter="url(#shadow)">
      <rect x="24" y="206" width="92" height="12" rx="3" fill="#ef4444" transform="rotate(-2 24 206)"/>
      <rect x="28" y="194" width="86" height="12" rx="3" fill="#3b82f6" transform="rotate(1 28 194)"/>
      <rect x="20" y="182" width="96" height="13" rx="3" fill="#f59e0b" transform="rotate(-1 20 182)"/>
    </g>
    <g filter="url(#shadow)">
      <path d="M170 240 Q214 225 258 240 L258 277 Q214 264 170 277 Z" fill="#fffdf7"/>
      <path d="M258 240 Q302 225 346 240 L346 277 Q302 264 258 277 Z" fill="#fffdf7"/>
    </g>
    <path d="M258 241 L258 275" stroke="#cbd5e1" stroke-width="2"/>
    <g stroke="#94a3b8" stroke-width="1.4" opacity=".65" fill="none">
      <path d="M184 248 Q212 241 244 248 M184 256 Q212 249 244 256 M184 264 Q212 257 244 264"/>
      <path d="M272 248 Q304 241 332 248 M272 256 Q304 249 332 256 M272 264 Q304 257 332 264"/>
    </g>
    <g filter="url(#shadow)">
      <rect x="384" y="236" width="38" height="38" rx="7" fill="#f8fafc"/>
      <path d="M422 244 Q438 253 422 266" stroke="#f8fafc" stroke-width="6" fill="none"/>
      <path d="M394 228 Q400 217 394 207 M407 228 Q413 217 407 207" stroke="#ffffff"
            stroke-width="2.5" fill="none" opacity=".75"/>
      <rect x="384" y="236" width="38" height="7" rx="3" fill="#94a3b8" opacity=".35"/>
    </g>
    <g transform="rotate(-12 356 278)">
      <rect x="348" y="250" width="7" height="34" rx="2" fill="#facc15"/>
      <path d="M348 284 L351.5 292 L355 284 Z" fill="#475569"/>
    </g>
    <g transform="translate(40 268)">
      <rect x="-14" y="16" width="28" height="20" rx="4" fill="#c2410c"/>
      <path d="M0 16 Q-22 2 -16 -16 Q0 -8 0 16 M0 16 Q22 2 16 -16 Q0 -8 0 16"
            fill="#22c55e"/>
    </g>
    """
    return "#fef3c7", "#fde68a", inner


def _scene_outdoor(rng):
    """Sunlit park: layered hills, trees, a winding path and wildflowers."""
    clouds = "".join(_cloud(x, y, s) for x, y, s in
                     ((90, 54, .55), (250, 42, .4), (180, 96, .32)))
    trees = "".join(_tree(x, y, s, tone) for x, y, s, tone in
                    ((56, 196, 1.05, "#15803d"), (126, 208, .78, "#16a34a"),
                     (400, 200, .95, "#166534"), (456, 214, .7, "#22c55e")))
    flowers = "".join(
        f'<g transform="translate({rng.randint(20, 460)} {rng.randint(236, 288)})">'
        f'<circle r="3.2" fill="{rng.choice(["#f472b6", "#fbbf24", "#ffffff"])}"/>'
        '<path d="M0 3 L0 11" stroke="#4ade80" stroke-width="1.5"/></g>'
        for _ in range(9)
    )
    birds = "".join(
        f'<path d="M{x} {y} q6 -5 12 0 q6 -5 12 0" stroke="#475569" stroke-width="1.8" '
        'fill="none" opacity=".7"/>'
        for x, y in ((300, 60), (338, 48), (365, 70))
    )
    inner = f"""
    {_sun(402, 62, 17)}{clouds}{birds}
    <path d="M0 158 Q80 100 156 158 Q226 106 300 158 Q380 112 480 158 L480 300 L0 300 Z"
          fill="#bbf7d0"/>
    <path d="M0 192 Q92 146 190 192 Q280 150 370 192 Q430 166 480 190 L480 300 L0 300 Z"
          fill="#86efac"/>
    <path d="M0 236 Q120 204 240 236 Q360 266 480 228 L480 300 L0 300 Z" fill="#4ade80"/>
    <path d="M188 300 Q214 244 262 228 Q310 244 344 300 Z" fill="#fde68a" opacity=".9"/>
    <path d="M196 300 Q220 250 262 234 Q306 250 332 300 Z" fill="#fef3c7" opacity=".55"/>
    {trees}{flowers}
    <g stroke="#22c55e" stroke-width="2" stroke-linecap="round" opacity=".8">
      <path d="M28 292 q3 -10 -2 -16 M74 286 q4 -12 0 -18 M424 288 q-4 -12 1 -18"/>
    </g>
    """
    return "#bae6fd", "#e0f2fe", inner


def _scene_water(rng):
    """A small sailboat on a calm sea with reflections and gulls."""
    clouds = "".join(_cloud(x, y, s, .75) for x, y, s in
                     ((86, 56, .5), (282, 44, .36), (300, 104, .3)))
    waves = "".join(
        f'<path d="M{y} {yy} q22 -8 44 0 t44 0 t44 0 t44 0 t44 0 t44 0 t44 0 t44 0" '
        f'stroke="#ffffff" stroke-width="2" fill="none" opacity="{op}"/>'
        for y, yy, op in ((0, 178, .45), (0, 202, .32), (0, 230, .28), (0, 262, .24))
    )
    birds = "".join(
        f'<path d="M{x} {y} q7 -6 14 0 q7 -6 14 0" stroke="#475569" stroke-width="1.8" '
        'fill="none" opacity=".65"/>'
        for x, y in ((318, 62), (355, 48), (388, 76))
    )
    sparkles = "".join(
        f'<circle cx="{rng.randint(70, 410)}" cy="{rng.randint(175, 285)}" '
        f'r="{rng.choice([1.5, 2, 2.5])}" fill="#ffffff" opacity=".6"/>'
        for _ in range(12)
    )
    inner = f"""
    {_sun(410, 66, 16)}{clouds}{birds}
    <rect x="0" y="164" width="480" height="136" fill="url(#sea)"/>
    <path d="M0 164 Q120 158 240 164 T480 164" stroke="#ffffff" stroke-width="2" fill="none" opacity=".6"/>
    {waves}
    <g filter="url(#shadow)">
      <path d="M286 200 L368 200 L354 224 L300 224 Z" fill="#dc2626"/>
      <rect x="325" y="128" width="4" height="72" rx="2" fill="#78350f"/>
      <path d="M329 132 L329 194 L372 194 Z" fill="#ffffff" opacity=".95"/>
      <path d="M322 138 L322 194 L292 194 Z" fill="#fef3c7"/>
    </g>
    <path d="M410 178 q-8 18 0 36 M426 180 q-6 14 0 28" stroke="#fbbf24"
          stroke-width="3" fill="none" opacity=".55"/>
    {sparkles}
    <path d="M0 280 Q120 264 240 282 Q360 298 480 272 L480 300 L0 300 Z"
          fill="#fde68a" opacity=".75"/>
    """
    return "#bae6fd", "#e0f2fe", inner


def _scene_night(rng):
    """Moonlit skyline with warm windows and a street lamp."""
    stars = "".join(
        f'<circle cx="{rng.randint(12, 468)}" cy="{rng.randint(12, 150)}" '
        f'r="{rng.choice([1, 1.4, 1.8, 2.2])}" fill="#ffffff" '
        f'opacity="{rng.choice([.45, .65, .85])}"/>'
        for _ in range(34)
    )
    buildings = ""
    x = 0
    while x < 480:
        w = rng.randint(42, 76)
        h = rng.randint(64, 138)
        y = 230 - h
        buildings += f'<rect x="{x}" y="{y}" width="{w - 5}" height="{h}" fill="#1e293b"/>'
        for wy in range(y + 12, 222, 20):
            for wx in range(x + 9, x + w - 12, 17):
                if rng.random() < .55:
                    buildings += (f'<rect x="{wx}" y="{wy}" width="6" height="8" rx="1" '
                                  'fill="#fbbf24" opacity=".85"/>')
        x += w
    inner = f"""
    {stars}
    <path d="M373 34 A34 34 0 1 0 407 90 A27 27 0 1 1 373 34 Z" fill="#fef9c3"/>
    <circle cx="388" cy="60" r="52" fill="url(#glow)"/>
    {buildings}
    <rect x="0" y="230" width="480" height="70" fill="#0f172a"/>
    <path d="M0 230 L480 230" stroke="#334155" stroke-width="2"/>
    <rect x="72" y="176" width="5" height="56" fill="#475569"/>
    <circle cx="74.5" cy="174" r="42" fill="#fde68a" opacity=".12"/>
    <circle cx="74.5" cy="174" r="9" fill="#fef9c3"/>
    <path d="M60 174 Q74 160 89 174 Z" fill="#334155"/>
    <ellipse cx="75" cy="248" rx="46" ry="9" fill="#fde68a" opacity=".08"/>
    """
    return "#0f172a", "#1e293b", inner


def _scene_abstract(rng):
    """Generic idea scene: an open book with floating light and petals."""
    bokeh = "".join(
        f'<circle cx="{rng.randint(20, 460)}" cy="{rng.randint(20, 280)}" '
        f'r="{rng.randint(8, 26)}" fill="#ffffff" opacity="{rng.choice([.08, .12, .18])}"/>'
        for _ in range(12)
    )
    petals = "".join(
        f'<path d="M{x} {y} q8 -12 16 0 q-8 12 -16 0 Z" fill="{rng.choice(["#f9a8d4", "#c4b5fd", "#fcd34d"])}" '
        'opacity=".75"/>'
        for x, y in ((68, 92), (392, 118), (110, 226), (372, 220), (240, 52))
    )
    inner = f"""
    {bokeh}{petals}
    <circle cx="240" cy="150" r="104" fill="#ffffff" opacity=".22"/>
    <circle cx="240" cy="150" r="76" fill="#ffffff" opacity=".20"/>
    <g filter="url(#shadow)">
      <path d="M138 156 Q190 136 240 156 L240 218 Q190 198 138 218 Z" fill="#fffdf7"/>
      <path d="M240 156 Q290 136 342 156 L342 218 Q290 198 240 218 Z" fill="#fffdf7"/>
    </g>
    <path d="M240 157 L240 216" stroke="#cbd5e1" stroke-width="2"/>
    <g stroke="#a5b4fc" stroke-width="1.6" opacity=".7" fill="none">
        <path d="M156 170 Q194 161 226 170 M156 180 Q194 171 226 180 M156 190 Q194 181 226 190"/>
        <path d="M254 170 Q292 161 324 170 M254 180 Q292 171 324 180 M254 190 Q292 181 324 190"/>
      </g>
    <g fill="#fbbf24">
      <path d="M240 108 l4 10 10 4 -10 4 -4 10 -4 -10 -10 -4 10 -4 Z"/>
      <circle cx="190" cy="122" r="3"/><circle cx="294" cy="118" r="3"/>
      <circle cx="208" cy="236" r="2.5"/><circle cx="278" cy="232" r="2.5"/>
    </g>
    """
    return "#ede9fe", "#fbcfe8", inner


def visual_scene_card(scene_text, word="", seed_text=""):
    """Deterministic illustrated scene card; the answer is never drawn on it."""
    seed = str(seed_text or word or scene_text or "?")
    rng = random.Random(sum(ord(c) for c in seed))
    theme = _scene_theme(scene_text)
    top, bottom, inner = {
        "study": lambda: _scene_study(rng),
        "outdoor": lambda: _scene_outdoor(rng),
        "water": lambda: _scene_water(rng),
        "night": lambda: _scene_night(rng),
        "abstract": lambda: _scene_abstract(rng),
    }[theme]()
    defs = f"""<defs>
      <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/>
      </linearGradient>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#7dd3fc"/><stop offset="1" stop-color="#e0f2fe"/>
      </linearGradient>
      <linearGradient id="wall" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#fde68a"/>
      </linearGradient>
      <linearGradient id="desk" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#b45309"/><stop offset="1" stop-color="#78350f"/>
      </linearGradient>
      <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#0369a1"/>
      </linearGradient>
      <linearGradient id="beam" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#fde68a"/><stop offset="1" stop-color="#fde68a" stop-opacity="0"/>
      </linearGradient>
      <radialGradient id="glow">
        <stop offset="0" stop-color="#fef9c3" stop-opacity=".3"/>
        <stop offset="1" stop-color="#fef9c3" stop-opacity="0"/>
      </radialGradient>
      <clipPath id="win"><rect x="299" y="31" width="150" height="124" rx="6"/></clipPath>
      <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
        <feDropShadow dx="0" dy="5" stdDeviation="5" flood-color="#000000" flood-opacity=".18"/>
      </filter>
      <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3"
        stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>
    </defs>"""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" '
        'viewBox="0 0 480 300">' + defs +
        '<rect width="480" height="300" rx="24" fill="url(#bg)"/>' + inner +
        '<rect width="480" height="300" rx="24" filter="url(#grain)" opacity=".05"/>'
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
