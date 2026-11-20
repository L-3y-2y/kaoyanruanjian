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
  - A session is capped at MAX_TURNS user messages; each reply uses a bounded
    token budget so a free API key is not burned by an idle chat.
"""

import json
import os

from db import execute, one, query, transaction
from llm import GenerationFailed, NoProvider, generate_text

MAX_TURNS = int(os.getenv("CHAT_MAX_TURNS", "8"))
MAX_REPLY_TOKENS = int(os.getenv("CHAT_MAX_TOKENS", "512"))
MAX_HISTORY = 12  # last N messages fed back to the model

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


def _messages_prompt(context, history, question):
    lines = [SYSTEM_PROMPT, _context_block(context), "CONVERSATION SO FAR:"]
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
    prompt = _messages_prompt(context, prior, question)
    answer, provider = generate_text(prompt, max_tokens=MAX_REPLY_TOKENS)

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
