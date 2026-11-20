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
  - It can also GENERATE kaoyan-style practice (cloze / reading / translation /
    writing) using today's learned words, fetched server-side on demand, and
    grade the student's answers. Still a side channel: no progress writes.
  - A session is capped at MAX_TURNS user messages; each reply uses a bounded
    token budget so a free API key is not burned by an idle chat.
"""

import json
import os
import re

from db import execute, one, query, transaction
from llm import GenerationFailed, NoProvider, generate_text

MAX_TURNS = int(os.getenv("CHAT_MAX_TURNS", "8"))
MAX_REPLY_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "512"))
PRACTICE_TOKENS = int(os.getenv("CHAT_PRACTICE_TOKENS", "1200"))
PRACTICE_TIMEOUT = int(os.getenv("CHAT_PRACTICE_TIMEOUT", "60"))
MAX_HISTORY = 12  # last N messages fed back to the model
PRACTICE_HINTS = (
    "出题", "出一篇", "做一篇", "选词填空", "完形", "考研题", "考研风格",
    "练一练", "练习", "测验", "测试我", "考我", "作文", "写一篇", "翻译练习",
    "阅读题", "批改", "打分", "给我题", "来道题",
)

SYSTEM_PROMPT = (
    "You are the English AI assistant inside a Chinese postgraduate "
    "entrance-exam vocabulary app. You help with ANY English-learning question: "
    "Chinese<->English translation, looking up a word (meaning, part of speech, "
    "pronunciation, collocations, example sentences), grammar and sentence "
    "structure, writing polish, and reading passages or questions when supplied.\n"
    "Rules you must never break:\n"
    "1. Stay on English-learning topics. If the question is unrelated to "
    "English, politely say you can only help with English and invite an "
    "English-related question.\n"
    "2. Language rule (STRICT):\n"
    "   - If the student gives Chinese and asks for English (怎么说/英语怎么说/"
    "translate to English), answer with the English word or phrase FIRST, then "
    "a short Chinese note. Example: '坚持 英语怎么说' -> 'persistent (adj.) / "
    "insist (v.) — 坚持'.\n"
    "   - If the student gives an English word or phrase and asks what it means "
    "(是什么意思/什么意思/translate to Chinese), answer IN CHINESE FIRST with "
    "the core Chinese meaning, then an optional short English note. Example: "
    "'abandon 是什么意思' -> 'abandon：动词，放弃；抛弃。例：He abandoned the plan. "
    "他放弃了计划。' Do NOT answer this kind of question mainly in English.\n"
    "   - Otherwise match the language the student used. Add bilingual examples "
    "when they help a Chinese learner.\n"
    "3. When a CONTEXT block is supplied, treat its passage/question and the "
    "saved word meanings as ground truth for questions about that material. "
    "Do NOT confine yourself to it: general vocabulary and grammar questions "
    "are allowed even with no passage selected.\n"
    "4. For a word lookup, include the English spelling, phonetic symbol if "
    "known, part of speech, core Chinese meaning, one natural example sentence "
    "with translation, and common collocations when relevant.\n"
    "5. Be concise but complete: normally under 160 words; translation and "
    "word lookup may be slightly longer when examples are needed. Never "
    "invent a definition that contradicts supplied ground truth.\n"
    "\n"
    "PRACTICE MODE (when the student asks for a quiz, exercise, essay or grading):\n"
    "- Honor exact counts requested by the student (e.g. 10 blanks or 5 "
    "questions). Do not silently produce fewer.\n"
    "- If a TODAY'S WORD BANK block is supplied, build the exercise from those "
    "words. If it says no plan exists, use standard 考研英语 vocabulary.\n"
    "- WRITE A BRAND-NEW PASSAGE. Never reuse, cloze, paraphrase or continue "
    "any passage shown in CONTEXT or in the 'PASSAGES ALREADY IN THE APP' "
    "list. Pick a different topic and setting and write original sentences. "
    "The exercise is NOT a blanked-out copy of an existing article.\n"
    "- Match real 考研英语 formats, not generic drills:\n"
    "  * 选词填空/完形填空: a 150-250 word passage with EXACTLY 10 numbered "
    "blanks (__1__ through __10__). List the word bank ONCE as options A-D "
    "before the passage; do NOT repeat an option list under every blank. Use "
    "ONLY words from TODAY'S WORD BANK (or their real inflections) as answers. "
    "Blanks must be plain __1__ with NO inline letters, answers or hints such "
    "as (A).\n"
    "  * 阅读理解: a 350-450 word passage plus 5 four-option questions "
    "(main idea / detail / inference / vocabulary), numbered 1-5, with "
    "answer key and short explanations.\n"
    "  * 翻译: a 考研-style English-to-Chinese task (5 underlined sentences "
    "or a short paragraph), with a reference translation at the end.\n"
    "  * 写作: 小作文 (letter/notice, about 100 words) or 大作文 "
    "(picture/chart, 160-200 words). Give the prompt, word limit and a short "
    "plan; when the student submits writing, score it 考研-style on content, "
    "organization and language, then give a corrected version.\n"
    "- Never reveal answers next to the questions. Put ALL answer keys and "
    "explanations ONLY in a final '参考答案与解析' section so the student can "
    "try first.\n"
    "- Always number items clearly and tell the student how to answer, e.g. "
    "'请按 1A 2C 3B ... 的格式回复' or '把作文全文发给我，我来批改'.\n"
    "- When grading, give a per-item verdict, explain why, and an overall "
    "score. Never claim any of this is recorded in the app's progress.\n"
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


def today_word_bank(conn, user_id, limit=40):
    """Words from the student's plan for today (read-only, never writes progress)."""
    plan = one(conn,
               "SELECT id FROM `daily_plan` WHERE user_id=%s AND study_date=CURDATE()",
               (user_id,))
    if not plan:
        return []
    rows = query(
        conn,
        """SELECT DISTINCT w.id, w.word, w.phonetic, w.translation, w.pos
           FROM `progress` p JOIN `word` w ON w.id=p.word_id
           WHERE p.user_id=%s AND p.daily_plan_id=%s
           ORDER BY w.id ASC LIMIT %s""",
        (user_id, plan["id"], limit),
    )
    bank = []
    for r in rows:
        meaning = (r["translation"] or "").replace("\n", " ")[:120]
        bank.append({"word": r["word"], "pos": r["pos"] or "",
                     "phonetic": r["phonetic"] or "", "meaning": meaning})
    return bank


def existing_articles_digest(conn, user_id, limit=8):
    """Titles/excerpts the generator must avoid, so exercises are never clones."""
    rows = query(
        conn,
        """SELECT title, LEFT(body, 260) AS excerpt FROM `article`
           WHERE user_id=%s ORDER BY id DESC LIMIT %s""",
        (user_id, limit),
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


def _practice_block(bank):
    if not bank:
        return ("TODAY'S WORD BANK: the student has no study plan for today. "
                "Use standard 考研英语 vocabulary instead.\n")
    lines = ["TODAY'S WORD BANK (use these words for any exercise):"]
    for w in bank:
        pos = f" {w['pos']}" if w["pos"] else ""
        lines.append(f"- {w['word']}{pos}: {w['meaning']}")
    return "\n".join(lines) + "\n"


def _messages_prompt(context, history, question, practice_block=""):
    lines = [SYSTEM_PROMPT, _context_block(context)]
    if practice_block:
        lines.append(practice_block)
    lines.append("CONVERSATION SO FAR:")
    for msg in history[-MAX_HISTORY:]:
        role = "Student" if msg["role"] == "user" else "Tutor"
        lines.append(f"{role}: {msg['content']}")
    lines.append(f"Student: {question}")
    lines.append("Tutor:")
    return "\n".join(lines)


def start_session(conn, user_id, source_type, source_id, context):
    context_json = json.dumps(context, ensure_ascii=False) if context else None
    label = context["label"] if context else "通用英语"
    execute(
        conn,
        """INSERT INTO `chat_session`
           (user_id, title, source_type, source_id, context)
           VALUES (%s,%s,%s,%s,%s)""",
        (user_id, label[:128], source_type or "general", source_id, context_json),
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


def reply(conn, session, user_id, question):
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
        bank = today_word_bank(conn, user_id)
        # Drop the article context on purpose: otherwise the model simply
        # blanks out the passage the student was reading.
        practice_block = _practice_block(bank) + existing_articles_digest(conn, user_id)
        effective_context = None
    else:
        practice_block = ""
        effective_context = context
    prompt = _messages_prompt(effective_context, prior, question, practice_block)
    budget = PRACTICE_TOKENS if is_practice else MAX_REPLY_TOKENS
    timeout = PRACTICE_TIMEOUT if is_practice else None
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
