"""AI English tutor — a strictly side-channel conversation.

Design rules (mirror docs/design-decisions.md):
  - Nothing here touches `progress`, `daily_plan`, `answer_log` or the two-level
    mastery states. Chat is practice only, like daily articles / weekly reading.
  - The model receives a FROZEN snapshot of the real article or question plus
    the saved translation snapshots, so it cannot invent word meanings.
  - The system prompt keeps it on English-learning topics but does NOT restrict
    it to a selected passage: translation (ZH<->EN), word lookup, grammar,
    writing polish and general English questions are all supported. When a
    passage/question is selected, its frozen snapshot is ground truth.
  - It can also GENERATE exam-style practice (cloze / reading / translation /
    writing) using today's learned words, fetched server-side on demand, and
    grade the student's answers. Still a side channel: no progress writes.
    The exam itself is not hard-coded: see exam_profile.py — a 四级 shelf gets
    四级 lengths, question types and grading.
  - A session is capped at MAX_TURNS user messages; each reply uses a bounded
    token budget so a free API key is not burned by an idle chat.
"""

import json
import os
import re

from db import detach_for_llm, execute, one, query, transaction
from exam_profile import profile_for
from llm import GenerationFailed, NoProvider, generate_json, generate_text

MAX_TURNS = int(os.getenv("CHAT_MAX_TURNS", "8"))
MAX_REPLY_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "512"))
PRACTICE_TOKENS = int(os.getenv("CHAT_PRACTICE_TOKENS", "1200"))
PRACTICE_TIMEOUT = int(os.getenv("CHAT_PRACTICE_TIMEOUT", "60"))
MAX_HISTORY = 12  # last N messages fed back to the model
# Structured practice cards are stored as assistant messages whose content begins
# with this marker followed by JSON. They render as interactive question cards in
# the browser, but stay in the chat history so follow-up questions keep context.
CARD_MARKER = "[[PRACTICE_CARD]]"
CARD_KINDS = ("cloze", "reading")
PRACTICE_HINTS = (
    "出题", "出一篇", "做一篇", "选词填空", "完形", "考研题", "考研风格",
    "练一练", "练习", "测验", "测试我", "考我", "作文", "写一篇", "翻译练习",
    "阅读题", "批改", "打分", "给我题", "来道题",
)

SYSTEM_PROMPT = (
    "你是一款面向中国英语考试备考学生的背单词 App 里的「英语助教」。你的用户都是中国人，"
    "母语是中文，当前在备考{{EXAM}}，英语水平还在提升中，不可能一上来就流畅阅读"
    "整段英文。你的本职工作是解答与英语有关的问题（翻译、查单词、语法、时态、"
    "语态、句型、写作润色、短文阅读和题目讲解），但你也要能正常地和人聊天交流。\n"
    "\n"
    "【第一条铁律：语言选择 —— 默认用中文】\n"
    "除了「英语本身作为教学素材」时使用英语之外，你所有的解释、寒暄、过渡、"
    "总结、引导，一律用中文。所谓教学素材，指：被查的单词、被教的短语、例句、"
    "被讲解的英文原句、翻译的目标文本、作文范文。\n"
    "绝对禁止：用一整段英文去回答一个用中文提出的问题。哪怕用户用中文问的是"
    "英语知识，你的讲解文字也必须是中文，英语只出现在单词和例句里。\n"
    "\n"
    "【按问题类型决定怎么答】\n"
    "1. 普通寒暄 / 闲聊（你是谁、你叫什么名字、你好、谢谢、今天天气、吃饭了没"
    "等与英语无关的日常话）：用中文简短、友好地回答，然后自然地引导回英语学习。"
    "例：「你叫什么名字？」→「我是你的英语学习助教，可以叫我小英～ 有什么英语"
    "问题想问我吗？」\n"
    "2. 与英语无关的其他问题（如写代码、生活建议等）：用中文礼貌说明你只擅长"
    "英语学习，并邀请对方问英语相关的问题。\n"
    "3. 英语知识讲解（单词意思 / 音标 / 词性、语法、时态、语态、句型、短语辨析、"
    "「为什么」「怎么用」「区别在哪」「这道题为什么选这个」等）：用中文讲解为主，"
    "配上必要的英语例句，例句后面附中文翻译。例：用户问「这个 was written 是什么"
    "语态？」→ 用中文说明这是「被动语态、过去时」，结构是「was/were + 过去分词」，"
    "再给 1~2 个英语例句并翻译。\n"
    "4. 中译英 / 查英文说法（「XX 的英语是什么 / 怎么说 / 怎么翻译」）：用中文"
    "讲解，把英语单词 / 短语放在最前面，再用中文解释词性、语境区别、常见搭配，"
    "必要时给例句。例：「传播的英语是什么」→「spread (v./n.) 传播、扩散（疾病/"
    "消息/火势）……」。绝不要只丢一个单词，也不要整段英语。\n"
    "5. 英译中 / 查意思（「XX 是什么意思」）：先用中文给出核心意思，再可附一个"
    "英语例句及翻译。\n"
    "6. 写作润色：给出修改后的英语文本，但用中文说明改了哪里、为什么。\n"
    "7. 用户明确指定语言（「用英语解释」「用中文回答」）：以用户指定为准。\n"
    "\n"
    "【内容边界与准确性】\n"
    "- 当提示里提供了 CONTEXT 块（短文或题目）时，把它当作该材料相关问题的唯一"
    "事实依据，不要凭空捏造词义；没有选材料时，也允许回答通用的词汇、语法、"
    "翻译、写作问题。\n"
    "- 查单词时，尽量给出：英文拼写、音标（若知道）、词性、核心中文释义、一个"
    "自然例句及翻译、常见搭配。\n"
    "- 简洁但完整：一般控制在 160 个中文字以内；翻译和查词因含例句可稍长。\n"
    "\n"
    "【出题 / 练习模式】（当学生要求出题、练习、作文或批改时）\n"
    "- 学生要求多少题就出多少题（例如 10 个空或 5 道题），不要悄悄少出。\n"
    "- 若提供了「今日词库 TODAY'S WORD BANK」，用这些词出题；若提示说今天没有"
    "学习计划，则使用标准{{EXAM}}词汇。\n"
    "- 必须写一篇全新的文章。禁止复用、挖空、改写或续写 CONTEXT 里或 "
    "「PASSAGES ALREADY IN THE APP」清单里出现过的任何文章。换个主题和场景，"
    "写原创句子。练习不是把已有文章挖空。\n"
    "- 贴合真实{{EXAM}}题型与难度，而不是通用练习。难度基准：{{LEVEL}}\n"
    "{{PRACTICE_TYPES}}\n"
    "- 以当前书架为准：学生如果要求别的考试的题（例如在四级书架里说「出考研题」），"
    "按当前书架出题，并用一句话说明「你现在学的是四级，我就按四级来出」。\n"
    "- 作文与翻译按{{EXAM}}的评分标准批改，不要套用别的考试的评分口径。\n"
    "- 绝不在题目旁边泄露答案。所有答案和解析只放在最后的「参考答案与解析」一节，"
    "让学生先自己做。\n"
    "- 题目编号要清晰，并告诉学生如何作答，例如「请按 1A 2C 3B ... 的格式回复」"
    "或「把作文全文发给我，我来批改」。\n"
    "- 批改时逐题给出判断和原因，再给总分。绝不要声称这些会被记入 App 的进度。\n"
    "\n"
    "【翻译题与作文题：先出题，等作答，再批改】\n"
    "- 学生要求出翻译题时，先明确告诉他是「汉译英」还是「英译汉」（也可以两种都出），"
    "然后只给出待翻译的句子，绝不先把参考译文写出来。\n"
    "- 学生要求出作文题时，只给出题目、字数要求与简短提纲，绝不先写范文。\n"
    "- 等学生把翻译或作文发回来，你再逐句比对、判分（满分 10 分），指出翻译/作文里"
    "语法、用词、结构上的具体不足，最后才给出参考译文或范文。\n"
    "- 如果学生只是请你「翻译这句话/这个词」，就直接翻译并简单讲解，不必让他先做。\n"
    "\n"
    "【格式要求：保持清爽，不要乱码符号】\n"
    "- 不要使用 Markdown 语法：不要出现 ** 加粗、* 斜体、# 标题、``` 代码块这类符号。"
    "需要强调时用中文引号「」或直接换行即可。\n"
    "- 用中文自然分段、条理清楚，让手机屏幕上的阅读体验干净舒服。\n"
)


def system_prompt(book=None):
    """Fill the exam slots so the tutor follows the shelf, not 考研 by default."""
    p = profile_for(book)
    return (SYSTEM_PROMPT
            .replace("{{EXAM}}", p["exam"])
            .replace("{{LEVEL}}", p["level"])
            .replace("{{PRACTICE_TYPES}}", p["practice_types"]))


def _book_block(book):
    """Tutor prompt fragment: pin the exam type to the selected shelf."""
    if not book:
        return ""
    p = profile_for(book)
    return (
        f"\n当前书架的考试类型：{p['exam']}。讲解深度、例句难度、题量与题型都按"
        f"{p['exam']}的标准执行，不要混入其他考试的要求。"
        f"不要在回复里提到这段说明或书名。\n"
    )


def _article_context(conn, article_id, user_id):
    art = one(conn, "SELECT * FROM `article` WHERE id=%s AND user_id=%s",
              (article_id, user_id))
    if not art:
        return None
    marks = query(
        conn,
        """SELECT surface, translation_snapshot, pos_snapshot FROM `article_word`
           WHERE article_id=%s ORDER BY position ASC""", (article_id,),
    )
    glossary = "; ".join(
        f"{m['surface']}={m['translation_snapshot'] or ''}" for m in marks
    )
    return {
        "title": art["title"] or "",
        "body": art["body"],
        "glossary": glossary,
        "label": "每日短文" if art["kind"] == "daily" else "周阅读文章",
    }


def _question_context(conn, question_id, user_id):
    q = one(conn, "SELECT * FROM `question` WHERE id=%s AND user_id=%s",
            (question_id, user_id))
    if not q:
        return None
    art = one(conn, "SELECT * FROM `article` WHERE id=%s AND user_id=%s",
              (q["article_id"], user_id))
    options = ""
    if q["options"]:
        try:
            parsed = json.loads(q["options"])
            options = "; ".join(f"{k}. {v}" for k, v in parsed.items())
        except (ValueError, AttributeError):
            options = str(q["options"])
    parts = [f"题目类型: {q['qtype']}", f"题干: {q['stem']}"]
    if options:
        parts.append(f"选项: {options}")
    if q["reference_answer"]:
        parts.append(f"参考答案要点: {q['reference_answer']}")
    if art:
        parts.append(f"文章标题: {art['title'] or ''}")
        parts.append(f"文章正文: {art['body']}")
    return {"label": "阅读题目", "detail": "\n".join(parts), "glossary": ""}


def build_context(conn, source_type, source_id, user_id):
    """Return a frozen context dict, or None for a general chat."""
    if source_type == "article" and source_id:
        return _article_context(conn, source_id, user_id)
    if source_type == "question" and source_id:
        return _question_context(conn, source_id, user_id)
    return None


def _context_block(context):
    if not context:
        return ("CONTEXT: no specific passage or question was selected. "
                "Answer general English questions (translation, vocabulary, "
                "grammar, writing) directly. Only if the student refers to "
                "'this passage' or 'this question' without one selected, ask "
                "them to open it first.\n")
    if "body" in context:
        return (
            f"CONTEXT ({context['label']}):\n"
            f"Title: {context['title']}\n"
            f"Passage:\n{context['body']}\n"
            f"Word meanings from the app (ground truth): {context['glossary']}\n"
        )
    return (
        f"CONTEXT ({context['label']}):\n{context['detail']}\n"
        f"Word meanings from the app (ground truth): {context['glossary']}\n"
    )


def today_word_bank(conn, user_id, book_id=1, limit=40):
    """Today's words from the CURRENT BOOK, read-only on progress."""
    plan = one(
        conn,
        """SELECT id FROM `daily_plan`
           WHERE user_id=%s AND book_id=%s AND study_date=CURDATE()""",
        (user_id, book_id),
    )
    if not plan:
        return []
    rows = query(
        conn,
        """SELECT DISTINCT w.id, w.word, w.phonetic, w.translation, w.pos
           FROM `progress` p JOIN `word` w ON w.id=p.word_id
           WHERE p.user_id=%s AND p.book_id=%s AND p.daily_plan_id=%s
           ORDER BY w.id ASC LIMIT %s""",
        (user_id, book_id, plan["id"], limit),
    )
    bank = []
    for r in rows:
        meaning = (r["translation"] or "").replace("\n", " ")[:120]
        bank.append({"word": r["word"], "pos": r["pos"] or "",
                     "phonetic": r["phonetic"] or "", "meaning": meaning})
    return bank


def existing_articles_digest(conn, user_id, book_id=1, limit=8):
    """Titles/excerpts from the CURRENT BOOK the generator must avoid."""
    rows = query(
        conn,
        """SELECT title, LEFT(body, 260) AS excerpt FROM `article`
           WHERE user_id=%s AND book_id=%s ORDER BY id DESC LIMIT %s""",
        (user_id, book_id, limit),
    )
    if not rows:
        return ""
    lines = ["PASSAGES ALREADY IN THE APP (do NOT reuse, cloze or paraphrase "
             "any of them; write a completely different passage):"]
    for r in rows:
        title = (r["title"] or "(untitled)").replace("\n", " ")[:80]
        excerpt = (r["excerpt"] or "").replace("\n", " ")[:220]
        lines.append(f"- {title}: {excerpt}")
    return "\n".join(lines) + "\n"


def wants_practice(question):
    return any(hint in question for hint in PRACTICE_HINTS)


def _practice_block(bank, book=None):
    exam = profile_for(book)["exam_en"]
    if not bank:
        return ("TODAY'S WORD BANK: the student has no study plan for today. "
                f"Use standard {exam} vocabulary instead.\n")
    lines = ["TODAY'S WORD BANK (use these words for any exercise):"]
    for w in bank:
        pos = f" {w['pos']}" if w["pos"] else ""
        lines.append(f"- {w['word']}{pos}: {w['meaning']}")
    return "\n".join(lines) + "\n"


def _card_schema_prompt(kind, book=None):
    """Strict JSON schema for an interactive practice card (shelf-aware)."""
    p = profile_for(book)
    lo, hi = p["cloze"]["words"] if kind == "cloze" else p["reading"]["words"]
    qn = len(p["reading"]["qtypes"])
    common = _book_block(book) + (
        "Return ONLY a JSON object, no prose, no markdown fences. "
        "Write a BRAND-NEW passage; never reuse, cloze or paraphrase any text in "
        "CONTEXT or in 'PASSAGES ALREADY IN THE APP'. Explanations are in Chinese.\n"
        f"Difficulty must match {p['exam_en']}: {p['level']}\n"
    )
    if kind == "cloze":
        return common + (
            "Schema exactly:\n"
            '{"kind":"cloze","title":"中文或英文标题","passage":"'
            f'{lo}-{hi} word English passage with exactly 10 blanks written as '
            '__1__ to __10__",'
            '"options":{"A":"word1","B":"word2",...,"J":"word10"},'
            '"blanks":[{"no":1,"answer":"A","explanation":"中文解析"},...]}\n'
            "Rules: options must have keys A-J (10 word-bank entries, each used "
            "exactly once); use ONLY TODAY'S WORD BANK words or their real "
            "inflections; blanks numbered 1-10 in order, each answer is one "
            "option key; every blank has a short Chinese explanation."
        )
    return common + (
        "Schema exactly:\n"
        '{"kind":"reading","title":"中文或英文标题","passage":"'
        f'an ORIGINAL English passage of {lo}-{hi} words '
        '(shorter passages are rejected)",'
        '"questions":[{"no":1,"stem":"English question",'
        '"options":{"A":"...","B":"...","C":"...","D":"..."},'
        '"answer":"B","explanation":"中文解析"},...]}\n'
        f"Rules: exactly {qn} questions in order, numbered 1-{qn}; "
        f"cover these question types: {', '.join(p['reading']['qtypes'])}. "
        "Each has four options A-D, one answer key, and a short Chinese "
        "explanation; do not reveal the answer in the stem. "
        f"The passage MUST reach at least {lo} words — write about "
        f"{max(3, round(lo / 60))} paragraphs of 4-6 sentences each and count the "
        "words before replying."
    )


def _card_valid(data, kind, book=None):
    if not isinstance(data, dict) or data.get("kind") != kind:
        return False
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        return False
    if not isinstance(data.get("passage"), str) or len(data["passage"]) < 100:
        return False
    p = profile_for(book)
    lo = (p["cloze"]["words"] if kind == "cloze" else p["reading"]["words"])[0]
    # Free-tier models reliably undershoot the word count when only the prompt
    # asks for it; half the shelf minimum is a hard reject (and a retry) without
    # making every second card fail.
    if len(data["passage"].split()) < int(lo * 0.5):
        return False
    if kind == "cloze":
        if len(re.findall(r"__\d+__", data["passage"])) != 10:
            return False
        opts = data.get("options")
        blanks = data.get("blanks")
        if not isinstance(opts, dict) or set(opts) != set("ABCDEFGHIJ"):
            return False
        if any(not isinstance(v, str) or not v.strip() for v in opts.values()):
            return False
        if not isinstance(blanks, list) or len(blanks) != 10:
            return False
        if {b.get("no") for b in blanks} != set(range(1, 11)):
            return False
        if any(b.get("answer") not in opts for b in blanks):
            return False
        if any(not str(b.get("explanation", "")).strip() for b in blanks):
            return False
        # Do NOT require answers to be unique: free-tier models sometimes reuse
        # a word, and that is harmless when each blank is its own A–J dropdown.
        return True
    questions = data.get("questions")
    want = len(profile_for(book)["reading"]["qtypes"])
    if not isinstance(questions, list) or len(questions) != want:
        return False
    if {q.get("no") for q in questions} != set(range(1, want + 1)):
        return False
    for q in questions:
        opts = q.get("options")
        if not isinstance(opts, dict) or set(opts) != {"A", "B", "C", "D"}:
            return False
        if any(not isinstance(v, str) or not v.strip() for v in opts.values()):
            return False
        if q.get("answer") not in opts:
            return False
        if not str(q.get("stem", "")).strip() or not str(q.get("explanation", "")).strip():
            return False
    return True


def _clean_para(text):
    """Strip numbering / quotes / markdown a model sometimes adds to a paragraph."""
    t = (text or "").strip()
    t = re.sub(r"^\s*(paragraph|para)\s*\d+\s*[:.\-]\s*", "", t, flags=re.I)
    t = t.strip().strip('"').strip("“”").strip("'").strip()
    t = re.sub(r"[*_#`]+", "", t)
    return t.strip()


def _paged_passage(bank, book):
    """A full-length reading passage, written one paragraph at a time.

    Asking a small/free model for a 300-word passage in one call returns ~110
    words no matter how the prompt words it (measured). Per-paragraph requests
    of 50-70 words each, chained on the running tail, actually reach the shelf's
    length.
    """
    p = profile_for(book)
    lo, _hi = p["reading"]["words"]
    total = max(3, min(8, round(lo / 45)))
    words = [w["word"].strip() for w in bank]
    groups = [[] for _ in range(total)]
    for i, w in enumerate(words):
        groups[i % total].append(w)

    parts = []
    for i in range(total):
        prev = " ".join(parts)[-600:]
        prompt = (
            f"You are writing paragraph {i + 1} of {total} of ONE continuous English "
            f"passage for a learner preparing for {p['exam_en']}. Length: 60-80 words. "
        )
        if parts:
            prompt += (f'The passage so far ends with: "...{prev}" Continue the same '
                       "topic naturally, without repeating earlier sentences. ")
        else:
            prompt += "This is the opening paragraph; introduce the topic clearly. "
        if groups[i]:
            prompt += ("Use these words naturally in this paragraph: "
                       + ", ".join(groups[i]) + ". ")
        prompt += (f"Difficulty: {p['level']} "
                   "Output ONLY the paragraph text — no title, no numbering, no "
                   "quotes, no commentary.")
        text, _prov = generate_text(prompt, max_tokens=520, timeout=60)
        cleaned = _clean_para(text)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts)


def _questions_valid(data, want):
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != want:
        return False
    if {q.get("no") for q in questions} != set(range(1, want + 1)):
        return False
    for q in questions:
        opts = q.get("options")
        if not isinstance(opts, dict) or set(opts) != {"A", "B", "C", "D"}:
            return False
        if any(not isinstance(v, str) or not v.strip() for v in opts.values()):
            return False
        if q.get("answer") not in opts:
            return False
        if not str(q.get("stem", "")).strip() or not str(q.get("explanation", "")).strip():
            return False
    return True


def _reading_questions(passage, bank, book):
    """Question set for an already-written passage (same shelf mix)."""
    p = profile_for(book)
    qtypes = p["reading"]["qtypes"]
    n = len(qtypes)
    words = ", ".join(w["word"] for w in bank)
    prompt = (
        f"You are an experienced {p['exam_en']} teacher. Read the passage below and "
        f"write exactly {n} multiple-choice questions that match the real "
        f"{p['exam_en']} paper. Question types in order: {', '.join(qtypes)}. "
        'Return ONLY JSON: {"questions":[{"no":1,"stem":"English question",'
        '"options":{"A":"...","B":"...","C":"...","D":"..."},"answer":"B",'
        '"explanation":"中文解析"}]}. Four plausible options each, never reveal the '
        "answer in the stem, thin the Chinese explanation to one or two sentences. "
        + (f"Use these words where natural: {words}. " if words else "")
        + "Difficulty must match the paper, not the passage's length.\n\nPassage:\n"
        + passage
    )
    data, _prov = generate_json(
        prompt, validator=lambda d: _questions_valid(d, n), max_tokens=900, timeout=60)
    return [
        {"no": q["no"], "stem": q["stem"].strip(),
         "options": {k: v.strip()[:120] for k, v in q["options"].items()},
         "answer": q["answer"],
         "explanation": str(q["explanation"]).strip()[:400]}
        for q in sorted(data["questions"], key=lambda x: x["no"])
    ]


def _reading_card(bank, book):
    """Two-step reading card: paged passage, then questions about THAT passage.

    Returns None when anything fails so the caller can fall back to the
    single-shot JSON path.
    """
    try:
        passage = _paged_passage(bank, book)
    except (GenerationFailed, NoProvider):
        return None
    if not passage or len(passage.split()) < 80:
        return None
    try:
        questions = _reading_questions(passage, bank, book)
    except (GenerationFailed, NoProvider, ValueError, KeyError, TypeError):
        return None
    if not questions:
        return None
    first = re.split(r"(?<=[.!?])\s+", passage)[0]
    short = " ".join(first.split()[:7]).rstrip(",;:.") + "…"
    return {"kind": "reading", "title": short or "阅读理解", "passage": passage,
            "questions": questions}


def generate_practice_card(conn, user_id, kind, book_id=1, book=None):
    """Generate one structured, validated practice card. Read-only on progress."""
    if kind not in CARD_KINDS:
        raise ValueError("不支持的题型")
    bank = today_word_bank(conn, user_id, book_id)
    digest = existing_articles_digest(conn, user_id, book_id)
    detach_for_llm(conn)  # word bank + digest read; release locks before LLM

    if kind == "reading":
        card = _reading_card(bank, book)
        if card:
            card["provider"] = "paged:" + (book.get("code") if book else "default")
            return card

    prompt = (
        _card_schema_prompt(kind, book) + "\n"
        + _practice_block(bank, book)
        + digest
        + "Student request: generate one " + ("cloze" if kind == "cloze" else "reading")
        + " exercise now.\nJSON:"
    )
    # Free-tier models occasionally miss a field or the exact count. Retry once
    # with a blunt reminder before giving up, so a transient slip is not fatal.
    data = None
    for attempt in range(2):
        attempt_prompt = prompt if attempt == 0 else (
            "Your previous reply failed validation. Return ONLY the JSON object "
            "with the EXACT field names and counts specified, ensuring every "
            "blank/question has all required fields (no, answer, explanation).\n\n"
            + prompt
        )
        try:
            data, provider = generate_json(
                attempt_prompt, validator=lambda d: _card_valid(d, kind, book),
                max_tokens=PRACTICE_TOKENS, timeout=PRACTICE_TIMEOUT,
            )
            break
        except (GenerationFailed, ValueError, KeyError, TypeError):
            if attempt == 1:
                raise
    # Normalise: keep only known fields, cap explanation length.
    card = {"kind": kind, "title": data["title"].strip()[:120],
            "passage": data["passage"].strip()}
    if kind == "cloze":
        card["options"] = {k: v.strip()[:80] for k, v in data["options"].items()}
        card["blanks"] = [
            {"no": b["no"], "answer": b["answer"],
             "explanation": str(b["explanation"]).strip()[:400]}
            for b in sorted(data["blanks"], key=lambda x: x["no"])
        ]
    else:
        card["questions"] = [
            {"no": q["no"], "stem": q["stem"].strip(),
             "options": {k: v.strip()[:120] for k, v in q["options"].items()},
             "answer": q["answer"],
             "explanation": str(q["explanation"]).strip()[:400]}
            for q in sorted(data["questions"], key=lambda x: x["no"])
        ]
    card["provider"] = provider
    return card


def send_card(conn, session, user_id, intent, card):
    """Store a practice card as a chat turn (user intent + marked assistant card)."""
    if session["turn_count"] >= MAX_TURNS:
        raise ValueError(f"这次对话已达 {MAX_TURNS} 轮上限，请关闭后重新开始")
    payload = CARD_MARKER + json.dumps(card, ensure_ascii=False)
    execute(conn, "INSERT INTO `chat_message` (session_id,user_id,role,content) "
                  "VALUES (%s,%s,'user',%s)", (session["id"], user_id, intent))
    execute(conn, "INSERT INTO `chat_message` (session_id,user_id,role,content) "
                  "VALUES (%s,%s,'assistant',%s)", (session["id"], user_id, payload))
    execute(conn, "UPDATE `chat_session` SET turn_count=turn_count+1 WHERE id=%s",
            (session["id"],))
    return {"role": "assistant", "card": card,
            "turn_count": session["turn_count"] + 1}


def _messages_prompt(context, history, question, practice_block="", book=None):
    lines = [system_prompt(book), _book_block(book), _context_block(context)]
    if practice_block:
        lines.append(practice_block)
    lines.append("CONVERSATION SO FAR:")
    for msg in history[-MAX_HISTORY:]:
        role = "Student" if msg["role"] == "user" else "Tutor"
        content = msg["content"]
        if content.startswith(CARD_MARKER):
            try:
                card = json.loads(content[len(CARD_MARKER):])
                summary = (f"[An interactive {card['kind']} exercise was shown: "
                           f"'{card.get('title','')}'. Correct answers and "
                           f"explanations: "
                           + json.dumps(card.get("blanks") or card.get("questions"),
                                        ensure_ascii=False) + "]")
                content = summary
            except (ValueError, KeyError, TypeError):
                content = "[an interactive exercise]"
        lines.append(f"{role}: {content}")
    lines.append(f"Student: {question}")
    lines.append("Tutor:")
    return "\n".join(lines)


def start_session(conn, user_id, source_type, source_id, context, book_id=1):
    context_json = json.dumps(context, ensure_ascii=False) if context else None
    label = context["label"] if context else "通用英语"
    execute(
        conn,
        """INSERT INTO `chat_session`
           (user_id, book_id, title, source_type, source_id, context)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (user_id, book_id, label[:128], source_type or "general",
         source_id, context_json),
    )
    sid = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
    return sid


def get_session(conn, session_id, user_id):
    return one(conn, "SELECT * FROM `chat_session` WHERE id=%s AND user_id=%s",
               (session_id, user_id))


def history(conn, session_id, user_id):
    if not get_session(conn, session_id, user_id):
        return None
    rows = query(
        conn,
        """SELECT role, content, created_at FROM `chat_message`
           WHERE session_id=%s AND user_id=%s ORDER BY id ASC""",
        (session_id, user_id),
    )
    return [{"role": r["role"], "content": r["content"],
             "created_at": str(r["created_at"])} for r in rows]


def list_sessions(conn, user_id, limit=20):
    return query(
        conn,
        """SELECT id, title, source_type, source_id, turn_count, updated_at
           FROM `chat_session` WHERE user_id=%s
           ORDER BY updated_at DESC LIMIT %s""",
        (user_id, limit),
    )


def reply(conn, session, user_id, question, book_id=1, book=None):
    """Append the student's question, generate a grounded tutor reply, store both.

    Raises ValueError (turn limit), NoProvider / GenerationFailed (LLM issues).
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    if len(question) > 2000:
        raise ValueError("问题太长了，请控制在 2000 字以内")
    if session["turn_count"] >= MAX_TURNS:
        raise ValueError(f"这次对话已达 {MAX_TURNS} 轮上限，请开始新的答疑")

    context = json.loads(session["context"]) if session["context"] else None
    prior = query(
        conn,
        """SELECT role, content FROM `chat_message`
           WHERE session_id=%s AND user_id=%s ORDER BY id ASC""",
        (session["id"], user_id),
    )
    is_practice = wants_practice(question) or any(
        wants_practice(m["content"]) for m in prior if m["role"] == "user"
    )
    if is_practice:
        bank = today_word_bank(conn, user_id, book_id)
        # Drop the article context on purpose: otherwise the model simply
        # blanks out the passage the student was reading.
        practice_block = (_practice_block(bank, book)
                          + existing_articles_digest(conn, user_id, book_id))
        effective_context = None
    else:
        practice_block = ""
        effective_context = context
    prompt = _messages_prompt(effective_context, prior, question,
                              practice_block, book)
    budget = PRACTICE_TOKENS if is_practice else MAX_REPLY_TOKENS
    timeout = PRACTICE_TIMEOUT if is_practice else None
    # History/session are read; release the transaction before the network call.
    detach_for_llm(conn)
    answer, provider = generate_text(prompt, max_tokens=budget, timeout=timeout)

    # Free-tier models sometimes produce only a handful of blanks. One bounded
    # retry with a blunt reminder keeps the exercise close to the exam format.
    if is_practice and "选词填空" in question:
        blanks = len(re.findall(r"__\d+__", answer))
        if blanks < 8:
            reminder = (
                f"\nSYSTEM REMINDER: your draft has only {blanks} blanks. "
                "Rewrite it with EXACTLY 10 blanks numbered __1__ to __10__. "
                "List the option bank A-D once, do not reveal answers beside "
                "blanks, and put the answer key at the very end."
            )
            retry_answer, retry_provider = generate_text(
                prompt + reminder, max_tokens=budget, timeout=timeout
            )
            if len(re.findall(r"__\d+__", retry_answer)) > blanks:
                answer, provider = retry_answer, retry_provider

    execute(conn, "INSERT INTO `chat_message` (session_id,user_id,role,content) "
                  "VALUES (%s,%s,'user',%s)", (session["id"], user_id, question))
    execute(conn, "INSERT INTO `chat_message` (session_id,user_id,role,content) "
                  "VALUES (%s,%s,'assistant',%s)", (session["id"], user_id, answer))
    execute(conn, "UPDATE `chat_session` SET turn_count=turn_count+1 WHERE id=%s",
            (session["id"],))
    return {"role": "assistant", "content": answer, "provider": provider,
            "turn_count": session["turn_count"] + 1}


def delete_session(conn, session_id, user_id):
    """Remove one conversation and all its messages (owned by this user only)."""
    if not get_session(conn, session_id, user_id):
        return False
    execute(conn, "DELETE FROM `chat_message` WHERE session_id=%s AND user_id=%s",
            (session_id, user_id))
    execute(conn, "DELETE FROM `chat_session` WHERE id=%s AND user_id=%s",
            (session_id, user_id))
    return True
