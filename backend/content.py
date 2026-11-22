"""Daily articles and weekly reading comprehension (requirement 4.1 - 4.5).

Both features are generated ONCE and persisted per user + batch / week.
They never write back to `progress` (A12 / 4.5). Generation is shared with the
configurable LLM layer; when no key is configured a deterministic local writer
produces readable passages with 100% target-word coverage, so the software is
fully usable offline.

Clickable marking works by scanning the saved body with a lemma map built from
ECDICT `exchange`, then storing a translation SNAPSHOT in article_word. That is
what makes plurals / past tense / sentence-initial capitals clickable and keeps
old articles showing the meaning they had when generated.
"""

import json
import re
from datetime import date, datetime, time, timedelta

from pymysql.err import IntegrityError

from db import detach_for_llm, execute, one, query
from llm import GenerationFailed, NoProvider, configured, generate_json, grade_subjective

WORDS_PER_ARTICLE = 12
MAX_ARTICLES = 3
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
SCENE_LABELS = {
    "default": "默认（不指定场景）",
    "campus": "校园生活",
    "lab": "实验室与科研",
    "workplace": "职场与实习",
    "news": "新闻与社会",
    "travel": "旅行与城市",
}
SCENES = {
    "default": "no special setting",
    "campus": "a university campus, classes, library and dorm life",
    "lab": "a university laboratory, experiments and research groups",
    "workplace": "an office, internship interviews and team work",
    "news": "a short news report about society or science",
    "travel": "travel, transport, hotels and unfamiliar cities",
}

OPENERS = [
    "The library was quiet that afternoon, and Lin had a whole table to herself.",
    "By the second week of term, the daily routine had started to feel familiar.",
    "Lin kept a notebook for the words that kept showing up in her reading.",
]
CLOSERS = [
    "By the time she closed the book, the passage felt less like a list of words.",
    "She underlined one last line and decided to read it again in the morning.",
    "Outside, the campus was noisy, but the sentences in her notebook were calm.",
]
# Fallback writer only: real contextual sentences instead of meta sentences like
# "the word X appears here". A light noun/verb/adjective guess keeps grammar
# plausible; the LLM path (when a key is set) does not use these at all.
_SENT_BY_POS = {
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
_SENT_GENERIC = [
    "That afternoon {w} came up again in a completely different text.",
    "In the margin she added a note about {w} and moved on.",
    "A classmate mentioned {w}, so she looked it up once more.",
]
SENTENCES = _SENT_GENERIC  # kept for backward compatibility with existing imports


# ---------------------------------------------------------------- word scanning

def _exchange_forms(row):
    forms = set()
    for part in (row.get("exchange") or "").split("/"):
        _, sep, value = part.partition(":")
        if sep and value:
            for item in value.split(","):
                item = item.strip().lower()
                if item and item.isascii() and " " not in item:
                    forms.add(item)
    return forms


def _fallback_forms(base):
    forms = {base + "s", base + "es", base + "ed", base + "d", base + "ing"}
    if base.endswith("y"):
        forms.add(base[:-1] + "ies")
        forms.add(base[:-1] + "ied")
    if base.endswith("e"):
        forms.add(base[:-1] + "ing")
    if len(base) > 3 and base[-1] not in "aeiou" and base[-2] in "aeiou" and base[-3] not in "aeiou":
        forms.add(base + base[-1] + "ing")
        forms.add(base + base[-1] + "ed")
    return {f for f in forms if f.isascii()}


def build_lemma_map(rows):
    """surface form (lower case) -> word row. Longest word wins on conflict."""
    mapping = {}
    for row in sorted(rows, key=lambda r: -len(r["word"])):
        base = row["word"].strip().lower()
        if not base:
            continue
        for form in {base} | _exchange_forms(row) | _fallback_forms(base):
            mapping.setdefault(form, row)
    return mapping


def scan_article_words(body, rows):
    """Return {surface, word_id, translation, phonetic, pos, position}."""
    lemma = build_lemma_map(rows)
    found, seen = [], set()
    for match in TOKEN_RE.finditer(body):
        token = match.group(0)
        row = lemma.get(token.lower())
        if not row or token in seen:
            continue
        seen.add(token)
        found.append({
            "surface": token, "word_id": row["id"],
            "translation": row.get("translation") or "",
            "phonetic": row.get("phonetic") or "",
            "pos": row.get("pos") or "",
            "position": match.start(),
        })
    # Phrasal verbs are treated as one word (4.3).
    for row in rows:
        base = row["word"].strip()
        if " " not in base:
            continue
        m = re.search(r"\b" + re.escape(base) + r"\b", body, re.IGNORECASE)
        if m and m.group(0) not in seen:
            seen.add(m.group(0))
            found.append({
                "surface": m.group(0), "word_id": row["id"],
                "translation": row.get("translation") or "",
                "phonetic": row.get("phonetic") or "",
                "pos": row.get("pos") or "",
                "position": m.start(),
            })
    return found


def segments_for(body, marked):
    """Split plain text into renderable segments. Called only when revealed=1."""
    if not marked:
        return [{"text": body, "word": None}]
    ordered = sorted(marked, key=lambda x: x["position"])
    parts, cursor = [], 0
    for item in ordered:
        start = item["position"]
        surface = item["surface"]
        real = body.find(surface, start)
        if real < 0 or real < cursor:
            continue
        if real > cursor:
            parts.append({"text": body[cursor:real], "word": None})
        parts.append({
            "text": surface,
            "word": {
                "word_id": item["word_id"], "surface": surface,
                "translation": item["translation"],
                "phonetic": item["phonetic"], "pos": item["pos"],
            },
        })
        cursor = real + len(surface)
    if cursor < len(body):
        parts.append({"text": body[cursor:], "word": None})
    return parts


# ---------------------------------------------------------------- text writing

def _pos_key(row):
    """Coarse noun/verb/adjective hint from the pos string, for sentence shape."""
    p = (row.get("pos") or "").lower()
    if p.startswith("v"):
        return "v"
    if p.startswith("adj") or p.startswith("a."):
        return "adj"
    if p.startswith("n"):
        return "n"
    return ""


def _local_article(words, seq):
    """Deterministic fallback: a small story-like passage, not a word list."""
    lines = [OPENERS[(seq - 1) % len(OPENERS)]]
    for i, row in enumerate(words):
        word = row["word"].strip()
        pool = _SENT_BY_POS.get(_pos_key(row), _SENT_GENERIC)
        template = pool[i % len(pool)]
        # Keep the target word in its natural position (mid-sentence) so the
        # sentence reads like real context; sentence-initial words still work.
        line = template.format(w=word)
        if not line.endswith("."):
            line += "."
        lines.append(line)
    # Word-form practice line: plurals / past tense really occur in the body,
    # so the clickable inflections are genuine rather than decorative.
    variants = []
    for row in words[:6]:
        base = row["word"].strip().lower()
        real = sorted(_exchange_forms(row) - {base})
        # Only show forms the dictionary actually attests. Rule-generated
        # guesses (abilitys, abandond) were worse than showing nothing.
        if real:
            variants.append(real[0])
    if variants:
        lines.append("Nearby forms: " + ", ".join(variants[:6]) + ".")
    lines.append(CLOSERS[(seq - 1) % len(CLOSERS)])
    return "\n".join(lines)


def _article_prompt(words, missing=None, scene="default"):
    word_list = "\n".join(
        f'- {r["word"].strip()} [{r.get("pos") or "word"}]: {r.get("translation") or ""}'
        for r in words
    )
    instruction = (
        "You write short English passages for Chinese postgraduate entrance exam "
        "students. Return ONLY a JSON object with exactly two keys: "
        '{"title": "short English title", "body": "the passage"}. '
        "Write ONE coherent passage of 120-160 words in simple, natural sentences; "
        "it must read like a passage, not a word list. Every target word below must "
        "appear at least once (inflected forms such as plurals or past tense are "
        "fine). Use the Chinese gloss only to choose the right meaning. No "
        "translations, bullet points, headings or commentary outside the JSON."
    )
    if scene and scene != "default":
        instruction += (" Set the passage in this scene: " +
                        SCENES.get(scene, SCENES["default"]) + ".")
    if missing:
        instruction += (
            "\nThe previous draft missed these words: " + ", ".join(missing) +
            ". Rewrite the whole passage so that every target word appears naturally."
        )
    return instruction + "\n\nTarget words:\n" + word_list


def _article_valid(data):
    if not isinstance(data, dict):
        return False
    body = data.get("body")
    return isinstance(body, str) and len(body.split()) >= 60


def _llm_article(words, seq, scene="default"):
    """Return (title, body, provider). Coverage uses the SAME lemma map as marking."""
    data, provider = generate_json(_article_prompt(words, scene=scene),
                                   validator=_article_valid, max_tokens=900)
    body = data["body"].strip()
    expected = {r["id"] for r in words}

    def covered(text):
        return {m["word_id"] for m in scan_article_words(text, words)}

    missing_ids = expected - covered(body)
    if missing_ids:
        missing = [r["word"] for r in words if r["id"] in missing_ids]
        data2, provider2 = generate_json(_article_prompt(words, missing, scene),
                                         validator=_article_valid, max_tokens=900)
        body2 = data2["body"].strip()
        if expected <= covered(body2):
            return (data2.get("title") or "").strip(), body2, provider2
        # Still incomplete: let write_article fall back to the local writer,
        # which guarantees 100% coverage, instead of bolting on robot sentences.
        raise GenerationFailed("大模型两次未能覆盖全部目标词")
    return (data.get("title") or "").strip(), body, provider


def write_article(conn, *, user_id, kind, plan_id, week_key, seq, words,
                  mode, uncovered, publish_at=None, deadline=None, scene="default"):
    # All inputs are already read by the caller. Release the read transaction
    # before the (slow) LLM call so the connection/locks are not held while we
    # wait on the network. The UNIQUE article keys below make the write idempotent.
    detach_for_llm(conn)
    llm_title = ""
    if configured():
        try:
            llm_title, body, provider = _llm_article(words, seq, scene)
        except (GenerationFailed, NoProvider):
            body, provider = _local_article(words, seq), "local"
    else:
        body, provider = _local_article(words, seq), "local"
    default_title = (f"第 {seq} 篇 · {len(words)} 词短文" if kind == "daily"
                     else f"第 {seq} 篇 · 本周阅读理解")
    title = llm_title or default_title
    try:
        execute(
            conn,
            """INSERT INTO `article`
               (user_id, kind, daily_plan_id, week_key, seq, title, body, mode, scene,
                covered_words, total_words, uncovered, revealed, publish_at,
                deadline, generator)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)""",
            (user_id, kind, plan_id, week_key, seq, title, body, mode, scene,
             len(words), len(words) + len(uncovered),
             json.dumps(uncovered, ensure_ascii=False), publish_at, deadline, provider),
        )
    except IntegrityError:
        # Another request generated this same article while our LLM call ran.
        # Return the row that won so we stay idempotent instead of crashing.
        row = one(
            conn,
            """SELECT id FROM `article` WHERE user_id=%s AND kind=%s
               AND daily_plan_id<=>%s AND week_key<=>%s AND seq=%s""",
            (user_id, kind, plan_id, week_key, seq),
        )
        if not row:
            raise
        return row["id"]
    article_id = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
    marked = scan_article_words(body, words)
    if marked:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT IGNORE INTO `article_word`
                   (article_id, user_id, word_id, surface, translation_snapshot,
                    phonetic_snapshot, pos_snapshot, position)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(article_id, user_id, m["word_id"], m["surface"],
                  m["translation"], m["phonetic"], m["pos"], m["position"])
                 for m in marked],
            )
    return article_id


def regenerate_article(conn, *, user_id, article_id):
    """Re-write one already generated daily article with the current generator.

    Keeps the same article id / plan / scene so the per-batch idempotence and
    existing links stay intact. Rebuilds article_word snapshots from the new
    body. Returns (title, provider). Raises GenerationFailed if no LLM key is
    configured (caller surfaces a clear message instead of silently overwriting
    a good local article with another local article).
    """
    if not configured():
        raise GenerationFailed("还没有配置大模型 Key，无法重新生成；请先填 backend/.env 的 LLM_API_KEY")
    art = one(
        conn,
        """SELECT * FROM `article` WHERE id=%s AND user_id=%s AND kind='daily'""",
        (article_id, user_id),
    )
    if not art:
        raise KeyError("article-not-found")
    scene = art.get("scene") or "default"
    plan_id = art["daily_plan_id"]
    # Recover the exact target words that were in this batch.
    rows = query(
        conn,
        """SELECT w.id, w.word, w.translation, w.pos, w.exchange
           FROM `progress` p JOIN `word` w ON w.id=p.word_id
           WHERE p.user_id=%s AND p.daily_plan_id=%s
           GROUP BY w.id ORDER BY MIN(p.id)""",
        (user_id, plan_id),
    )
    if not rows:
        raise KeyError("batch-words-missing")
    detach_for_llm(conn)  # inputs read; do not hold locks during the LLM call
    title, body, provider = _llm_article(rows, art["seq"], scene)
    if not title:
        title = f"第 {art['seq']} 篇 · {len(rows)} 词短文"
    execute(
        conn,
        """UPDATE `article` SET title=%s, body=%s, generator=%s,
                  covered_words=%s, total_words=%s WHERE id=%s""",
        (title, body, provider, len(rows), len(rows), article_id),
    )
    execute(conn, "DELETE FROM `article_word` WHERE article_id=%s", (article_id,))
    marked = scan_article_words(body, rows)
    if marked:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO `article_word`
                   (article_id, user_id, word_id, surface, translation_snapshot,
                    phonetic_snapshot, pos_snapshot, position)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(article_id, user_id, m["word_id"], m["surface"],
                  m["translation"], m["phonetic"], m["pos"], m["position"])
                 for m in marked],
            )
    return title, provider


# ---------------------------------------------------------------- daily articles

def daily_batches(conn, user_id):
    rows = query(
        conn,
        """SELECT dp.id AS plan_id, dp.study_date, dp.word_count, dp.stage,
                  (SELECT COUNT(*) FROM `article` a
                    WHERE a.user_id=dp.user_id AND a.kind='daily'
                      AND a.daily_plan_id=dp.id) AS article_count
           FROM `daily_plan` dp
           WHERE dp.user_id=%s AND dp.stage IN ('memorized','mastered')
           ORDER BY dp.study_date ASC, dp.id ASC""",
        (user_id,),
    )
    for row in rows:
        row["study_date"] = str(row["study_date"])
    return rows


def _plan_words(conn, user_id, plan_id):
    return query(
        conn,
        """SELECT DISTINCT w.id, w.word, w.phonetic, w.translation, w.pos,
                  w.exchange, p.wrong_count
           FROM `progress` p JOIN `word` w ON w.id=p.word_id
           WHERE p.user_id=%s AND p.daily_plan_id=%s
           ORDER BY w.id ASC""",
        (user_id, plan_id),
    )


def ensure_daily_articles(conn, user_id, plan_id, scene="default"):
    """Generate once per mastered batch. Repeated calls return the SAME articles."""
    plan = one(
        conn,
        "SELECT * FROM `daily_plan` WHERE id=%s AND user_id=%s",
        (plan_id, user_id),
    )
    if not plan:
        return None, "找不到该日期批次"
    if plan["stage"] not in ("memorized", "mastered"):
        return None, "短文只在学完当批次的 5 轮后才出现"
    existing = query(
        conn,
        """SELECT * FROM `article` WHERE user_id=%s AND kind='daily'
           AND daily_plan_id=%s ORDER BY seq ASC""",
        (user_id, plan_id),
    )
    if existing:
        return existing, None
    words = _plan_words(conn, user_id, plan_id)
    if not words:
        return None, "这个批次没有单词记录"
    capacity = WORDS_PER_ARTICLE * MAX_ARTICLES
    mode = "full"
    uncovered = []
    chosen = words
    if len(words) > capacity:
        mode = "sample"
        # B5: prefer words the user struggled with, and rotate so every word
        # eventually gets picked at least once across generations.
        cursor = one(conn, "SELECT sample_cursor AS c FROM `user` WHERE id=%s",
                     (user_id,))
        offset = (cursor["c"] if cursor else 0) % len(words)
        rotated = words[offset:] + words[:offset]
        ranked = sorted(rotated, key=lambda r: (-int(r["wrong_count"] or 0), r["id"]))
        chosen = ranked[:capacity]
        execute(conn, "UPDATE `user` SET sample_cursor=sample_cursor+%s WHERE id=%s",
                (capacity, user_id))
        picked = {w["id"] for w in chosen}
        uncovered = [w["word"] for w in words if w["id"] not in picked]
    chunks = [chosen[i:i + WORDS_PER_ARTICLE]
              for i in range(0, len(chosen), WORDS_PER_ARTICLE)]
    for seq, chunk in enumerate(chunks, start=1):
        write_article(conn, user_id=user_id, kind="daily", plan_id=plan_id,
                      week_key=None, seq=seq, words=chunk, mode=mode,
                      uncovered=uncovered,
                      scene=scene if scene in SCENES else "default")
    return query(
        conn,
        """SELECT * FROM `article` WHERE user_id=%s AND kind='daily'
           AND daily_plan_id=%s ORDER BY seq ASC""",
        (user_id, plan_id),
    ), None


def article_segments(conn, article):
    """Build the clickable layer from saved snapshots, without persisting reveal."""
    marked = query(
        conn,
        """SELECT surface, word_id, translation_snapshot, phonetic_snapshot,
                  pos_snapshot, position FROM `article_word`
           WHERE article_id=%s ORDER BY position ASC""",
        (article["id"],),
    )
    words = [{"surface": m["surface"], "word_id": m["word_id"],
              "translation": m["translation_snapshot"],
              "phonetic": m["phonetic_snapshot"], "pos": m["pos_snapshot"],
              "position": m["position"]} for m in marked]
    return segments_for(article["body"], words)


def article_payload(conn, article):
    marked = query(
        conn,
        """SELECT surface, word_id, translation_snapshot, phonetic_snapshot,
                  pos_snapshot, position FROM `article_word`
           WHERE article_id=%s ORDER BY position ASC""",
        (article["id"],),
    )
    words = [{"surface": m["surface"], "word_id": m["word_id"],
              "translation": m["translation_snapshot"],
              "phonetic": m["phonetic_snapshot"], "pos": m["pos_snapshot"],
              "position": m["position"]} for m in marked]
    # Reveal is now a per-view action, not a persisted state. Every visit starts
    # with plain text; the reader clicks "显示今日词" to get the clickable layer.
    # The translation snapshots stay in article_word, so nothing is lost.
    return {
        "id": article["id"], "seq": article["seq"], "title": article["title"],
        "body": article["body"], "mode": article["mode"],
        "covered_words": article["covered_words"],
        "total_words": article["total_words"],
        "uncovered": json.loads(article["uncovered"] or "[]"),
        "revealed": False,
        "scene": article.get("scene") or "default",
        "generator": article["generator"],
        "created_at": str(article["created_at"]),
        "segments": None,
        "marked_count": len(words),
    }


# ---------------------------------------------------------------- weekly reading

def week_key_of(day):
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_bounds(week_key):
    year, week = week_key.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    friday = datetime.combine(monday + timedelta(days=4), time(18, 0))
    sunday = datetime.combine(monday + timedelta(days=6), time(23, 59, 59))
    return monday, monday + timedelta(days=6), friday, sunday


def week_words(conn, user_id, week_key):
    monday, sunday, _, _ = week_bounds(week_key)
    rows = query(
        conn,
        """SELECT DISTINCT w.id, w.word, w.phonetic, w.translation, w.pos,
                  w.exchange, 0 AS wrong_count
           FROM `daily_plan` dp
           JOIN `progress` p ON p.daily_plan_id=dp.id AND p.user_id=dp.user_id
           JOIN `word` w ON w.id=p.word_id
           WHERE dp.user_id=%s AND dp.stage='mastered'
             AND dp.study_date BETWEEN %s AND %s
           ORDER BY w.id ASC""",
        (user_id, monday, sunday),
    )
    if not rows:
        # MySQL 8 rejects ORDER BY a column that is not in a DISTINCT select
        # list (error 3065). Use an IN subquery instead: it keeps the words
        # unique and still scopes them to this user's mastered batches.
        rows = query(
            conn,
            """SELECT w.id, w.word, w.phonetic, w.translation, w.pos,
                      w.exchange, 0 AS wrong_count
               FROM `word` w
               WHERE w.id IN (
                 SELECT p.word_id FROM `progress` p
                 JOIN `daily_plan` dp ON dp.id=p.daily_plan_id
                 WHERE dp.user_id=%s AND dp.stage='mastered'
               )
               ORDER BY w.id ASC LIMIT 40""",
            (user_id,),
        )
    return rows


def _template_questions(article, words):
    """Fallback set: four objective questions + one AI-graded short answer."""
    items = []
    picked = [w for w in words if w.get("translation")][:8]
    distractor_pool = [w["translation"].split(";")[0].split("，")[0].strip()
                       for w in picked if w.get("translation")]

    def options(right, pool, idx):
        others = [x for x in pool if x and x != right]
        filler = ["一种学习方法", "一段校园对话", "一个实验结果", "一处地理名称",
                  "一类社会关系", "一项政策说明"]
        picked_others = []
        for k in range(3):
            if others:
                picked_others.append(others[(idx + k) % len(others)])
            else:
                picked_others.append(filler[(idx + k) % len(filler)])
        vals = [right] + picked_others[:3]
        order = ["A", "B", "C", "D"]
        # deterministic shuffle so the answer is not always A
        shift = idx % 4
        vals = vals[shift:] + vals[:shift]
        return {order[i]: vals[i] for i in range(len(vals))}, order[vals.index(right)]

    seq = article["seq"]
    items.append({
        "qtype": "main_idea", "sort_no": 1,
        "stem": "What is the main purpose of this passage?",
        "options": {
            "A": "To review recently mastered vocabulary in simple context",
            "B": "To argue that memorisation is the only useful study method",
            "C": "To introduce a new grammar rule",
            "D": "To compare two examination systems",
        },
        "correct_option": "A", "reference_answer": None, "word_id": None,
        "ai_graded": 0,
    })
    if picked:
        w = picked[min(seq, len(picked) - 1)]
        meaning = (w["translation"] or "").split(";")[0].split("，")[0].strip()
        opts, correct = options(meaning, distractor_pool, seq + 1)
        items.append({
            "qtype": "vocabulary", "sort_no": 2,
            "stem": f"In the passage, the word '{w['word']}' most probably means ____.",
            "options": opts, "correct_option": correct,
            "reference_answer": None, "word_id": w["id"], "ai_graded": 0,
        })
        w2 = picked[0]
        items.append({
            "qtype": "detail", "sort_no": 3,
            "stem": f"Which of the following words is explicitly mentioned in the passage?",
            "options": {"A": w2["word"], "B": "photosynthesis", "C": "telescope",
                        "D": "architecture"},
            "correct_option": "A", "reference_answer": None,
            "word_id": w2["id"], "ai_graded": 0,
        })
        items.append({
            "qtype": "inference", "sort_no": 4,
            "stem": "According to the closing sentence, what is the reader advised to do?",
            "options": {"A": "Read the passage once more and recall the meanings",
                        "B": "Skip every unfamiliar word",
                        "C": "Translate the whole passage word for word",
                        "D": "Memorise the title only"},
            "correct_option": "A", "reference_answer": None,
            "word_id": None, "ai_graded": 0,
        })
        items.append({
            "qtype": "subjective", "sort_no": 5,
            "stem": f"用中文写出 '{w2['word']}' 在文中的意思，并说明你依据什么判断（简答题，AI 判分）。",
            "options": None, "correct_option": None,
            "reference_answer": (w2["translation"] or "").strip()[:300],
            "word_id": w2["id"], "ai_graded": 1,
        })
    return items


def _question_prompt(article, words):
    word_list = ", ".join(r["word"].strip() for r in words)
    return (
        "You are an experienced Chinese postgraduate entrance exam English teacher. "
        "Read the passage and write 5 exam-style questions. Return ONLY a JSON object "
        "with one key \"questions\" containing exactly 5 objects, one of each qtype: "
        "main_idea, vocabulary, detail, inference, subjective. Each object has: "
        "\"qtype\", \"stem\" (English), and for the four objective types also "
        "\"options\" (object with keys A,B,C,D, four plausible choices) and "
        "\"correct_option\" (A/B/C/D). The subjective object instead has "
        "\"reference_answer\" (Chinese key points for grading). For vocabulary, "
        "detail and subjective, also include \"word\" copied exactly from the target "
        "word list. Distractors must be plausible and related to the passage; do not "
        "reveal the answer in the stem. Do not include any text outside the JSON.\n\n"
        f"Target words: {word_list}\n\nPassage:\n{article['body']}"
    )


def _questions_valid(data):
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return False
    qs = data["questions"]
    if len(qs) != 5:
        return False
    if {q.get("qtype") for q in qs} != {
            "main_idea", "vocabulary", "detail", "inference", "subjective"}:
        return False
    for q in qs:
        if not isinstance(q.get("stem"), str) or not q["stem"].strip():
            return False
        if q["qtype"] == "subjective":
            if not isinstance(q.get("reference_answer"), str) or not q["reference_answer"].strip():
                return False
        else:
            opts = q.get("options")
            if not isinstance(opts, dict) or set(opts) != {"A", "B", "C", "D"}:
                return False
            if any(not isinstance(v, str) or not v.strip() for v in opts.values()):
                return False
            if q.get("correct_option") not in ("A", "B", "C", "D"):
                return False
    return True


def _llm_questions(article, words):
    data, _provider = generate_json(_question_prompt(article, words),
                                    validator=_questions_valid, max_tokens=1600)
    items = []
    lower = {r["word"].strip().lower(): r["id"] for r in words}
    picked = [w for w in words if w.get("translation")]
    for sort_no, q in enumerate(data["questions"], start=1):
        qtype = q["qtype"]
        target = str(q.get("word") or "").strip().lower()
        word_id = lower.get(target)
        if qtype in ("vocabulary", "detail", "subjective") and word_id is None and picked:
            word_id = picked[0]["id"]
        items.append({
            "qtype": qtype, "sort_no": sort_no, "stem": q["stem"].strip(),
            "options": (None if qtype == "subjective"
                        else {k: v.strip() for k, v in q["options"].items()}),
            "correct_option": None if qtype == "subjective" else q["correct_option"],
            "reference_answer": (q["reference_answer"].strip()[:300]
                                 if qtype == "subjective" else None),
            "word_id": word_id, "ai_graded": 1 if qtype == "subjective" else 0,
        })
    return items


def _questions_for(conn, article_id, user_id, article, words):
    """Prefer LLM-generated questions; keep the old template set as fallback."""
    # The article row is already written; commit it and release the lock before
    # the question-generation network call.
    detach_for_llm(conn)
    items = None
    if configured():
        try:
            items = _llm_questions(article, words)
        except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
            items = None
    if items is None:
        items = _template_questions(article, words)
    # Avoid duplicate question sets if a racing request already filled this
    # article while our LLM call was running.
    if one(conn, "SELECT id FROM `question` WHERE article_id=%s LIMIT 1", (article_id,)):
        return
    for item in items:
        execute(
            conn,
            """INSERT IGNORE INTO `question`
               (article_id, user_id, qtype, stem, options, correct_option,
                reference_answer, word_id, ai_graded, sort_no)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (article_id, user_id, item["qtype"], item["stem"],
             json.dumps(item["options"], ensure_ascii=False) if item["options"] else None,
             item["correct_option"], item["reference_answer"], item["word_id"],
             item["ai_graded"], item["sort_no"]),
        )


def ensure_weekly(conn, user_id, week_key=None, force=False):
    """Friday-evening publish + open-time fallback (B5)."""
    now = datetime.now()
    key = week_key or week_key_of(now.date())
    monday, sunday, publish_at, deadline = week_bounds(key)
    existing = query(
        conn,
        """SELECT * FROM `article` WHERE user_id=%s AND kind='weekly'
           AND week_key=%s ORDER BY seq ASC""",
        (user_id, key),
    )
    if existing:
        return {"status": "published", "week_key": key,
                "publish_at": str(publish_at), "deadline": str(deadline),
                "articles": existing, "locked": False}
    if not force and now < publish_at:
        return {"status": "not_published", "week_key": key,
                "publish_at": str(publish_at), "deadline": str(deadline),
                "articles": [], "locked": True}
    words = week_words(conn, user_id, key)
    if not words:
        words = query(
            conn,
            "SELECT id, word, phonetic, translation, pos, exchange, 0 AS wrong_count "
            "FROM `word` ORDER BY id ASC LIMIT 12",
        )
    capacity = WORDS_PER_ARTICLE * 2
    mode, uncovered, chosen = "full", [], words
    if len(words) > capacity:
        mode = "sample"
        chosen = words[:capacity]
        uncovered = [w["word"] for w in words[capacity:]]
    halves = [chosen[:WORDS_PER_ARTICLE], chosen[WORDS_PER_ARTICLE:]]
    halves = [h for h in halves if h]
    while len(halves) < 2:
        halves.append(chosen[:WORDS_PER_ARTICLE])
    for seq, chunk in enumerate(halves[:2], start=1):
        aid = write_article(conn, user_id=user_id, kind="weekly", plan_id=None,
                            week_key=key, seq=seq, words=chunk, mode=mode,
                            uncovered=uncovered, publish_at=publish_at,
                            deadline=deadline)
        art = one(conn, "SELECT * FROM `article` WHERE id=%s", (aid,))
        _questions_for(conn, aid, user_id, art, chunk)
    return {"status": "published", "week_key": key,
            "publish_at": str(publish_at), "deadline": str(deadline),
            "articles": query(
                conn,
                """SELECT * FROM `article` WHERE user_id=%s AND kind='weekly'
                   AND week_key=%s ORDER BY seq ASC""",
                (user_id, key)), "locked": False}


def reading_payload(conn, user_id, week_key):
    result = ensure_weekly(conn, user_id, week_key)
    if result["status"] != "published":
        return result
    now = datetime.now()
    _, _, _, deadline = week_bounds(week_key)
    late = now > deadline
    articles = []
    for art in result["articles"]:
        questions = query(
            conn,
            """SELECT q.id, q.qtype, q.stem, q.options, q.sort_no, q.ai_graded,
                      a.user_answer, a.correct, a.score, a.ai_result, a.scored,
                      CASE WHEN a.id IS NOT NULL THEN q.correct_option END AS correct_option,
                      CASE WHEN a.id IS NOT NULL THEN q.reference_answer END AS standard_answer
               FROM `question` q
               LEFT JOIN `answer_log` a
                 ON a.question_id=q.id AND a.user_id=q.user_id AND a.kind='exam'
               WHERE q.article_id=%s AND q.user_id=%s
               ORDER BY q.sort_no ASC""",
            (art["id"], user_id),
        )
        for q in questions:
            q["options"] = json.loads(q["options"]) if q["options"] else None
            q["answered"] = q["user_answer"] is not None
        articles.append({
            "id": art["id"], "seq": art["seq"], "title": art["title"],
            "body": art["body"], "mode": art["mode"],
            "uncovered": json.loads(art["uncovered"] or "[]"),
            "questions": questions,
        })
    answered = sum(1 for a in articles for q in a["questions"] if q["answered"])
    total = sum(len(a["questions"]) for a in articles)
    correct = sum(1 for a in articles for q in a["questions"] if q["correct"] == 1)
    scores = [float(q["score"]) for a in articles for q in a["questions"]
              if q["score"] is not None and q["scored"]]
    return {
        "status": "published", "week_key": week_key,
        "publish_at": result["publish_at"], "deadline": result["deadline"],
        "late": late, "articles": articles,
        "summary": {"answered": answered, "total": total, "correct": correct,
                    "score": round(sum(scores), 2), "scored": bool(scores)},
    }


def _exam_existing(conn, user_id, question_id):
    return one(
        conn,
        """SELECT * FROM `answer_log` WHERE user_id=%s AND kind='exam'
           AND question_id=%s ORDER BY id DESC LIMIT 1""",
        (user_id, question_id),
    )


def _exam_result_from(row):
    return {"duplicate": True, "correct": row["correct"],
            "score": float(row["score"]) if row["score"] is not None else None,
            "reason": row["ai_result"], "standard_answer": None}


def grade_answer(conn, user_id, question, value, week_key, late):
    """Write one exam answer. Idempotent per question, even under concurrency.

    The unique key uk_exam_question(user_id, question_id) is the final guard:
    two simultaneous submissions can both pass the initial SELECT, but only
    one INSERT wins — the loser catches IntegrityError and returns the stored
    answer instead of double-writing (or crashing the whole request).
    """
    existing = _exam_existing(conn, user_id, question["id"])
    if existing:
        return _exam_result_from(existing)

    if question["ai_graded"]:
        score, reason, provider = grade_subjective(
            question["reference_answer"] or "", value
        )
        correct = 1 if score >= 6 else 0
        insert_args = (
            user_id, question["id"], question["article_id"], week_key, value,
            correct, score, 0 if late else 1, 1 if late else 0,
            json.dumps({"reason": reason, "provider": provider}, ensure_ascii=False),
        )
        sql = """INSERT INTO `answer_log`
               (user_id, kind, question_id, article_id, week_key, action,
                user_answer, correct, score, scored, is_late, ai_result)
               VALUES (%s,'exam',%s,%s,%s,'subjective',%s,%s,%s,%s,%s,%s)"""
        fresh = {"duplicate": False, "correct": correct, "score": score,
                 "reason": reason, "standard_answer": question["reference_answer"]}
    else:
        correct = 1 if (value or "").strip().upper() == (question["correct_option"] or "").upper() else 0
        score = 10.0 if correct else 0.0
        insert_args = (
            user_id, question["id"], question["article_id"], week_key, value,
            correct, score, 0 if late else 1, 1 if late else 0,
        )
        sql = """INSERT INTO `answer_log`
               (user_id, kind, question_id, article_id, week_key, action,
                user_answer, correct, score, scored, is_late)
               VALUES (%s,'exam',%s,%s,%s,'objective',%s,%s,%s,%s,%s)"""
        fresh = {"duplicate": False, "correct": bool(correct), "score": score,
                 "reason": None, "standard_answer": question["correct_option"]}

    # INSERT IGNORE: on uk_exam_question collision it silently skips (rowcount
    # 0) instead of raising 1062, so the caller's transaction stays healthy.
    # rowcount 1 means WE inserted; 0 means a concurrent request already stored
    # this answer, so return the saved result as a duplicate.
    ignore_sql = sql.replace("INSERT INTO", "INSERT IGNORE INTO", 1)
    rowcount = execute(conn, ignore_sql, insert_args)
    if rowcount == 0:
        stored = _exam_existing(conn, user_id, question["id"])
        if stored:
            return _exam_result_from(stored)
    return fresh
