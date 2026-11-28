"""AI 英语教练（侧通道）：对话、历史、练习卡片、删除。

从 main.py 拆出的路由模块，路径前缀不变。这里只写会话与卡片，
绝不写 progress / answer_log / 五轮状态。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from db import transaction
from llm import GenerationFailed, NoProvider, configured as llm_configured
from study_state import book_row
import chat as chat_service

router = APIRouter()


class ChatStartIn(BaseModel):
    source_type: str = "general"
    source_id: int | None = None
    first_question: str = ""


class ChatAskIn(BaseModel):
    session_id: int
    question: str


class ChatSessionIn(BaseModel):
    session_id: int


class ChatPracticeIn(BaseModel):
    kind: str  # cloze | reading
    session_id: int | None = None
    source_type: str = "general"
    source_id: int | None = None


@router.post("/api/chat/practice")
def chat_practice(req: ChatPracticeIn, user=Depends(current_user)):
    """Generate a structured, interactive practice card (cloze / reading).

    The card is stored as a marked assistant message in the chat session, so it
    stays in context for follow-up questions and is still a strict side channel:
    nothing here writes progress / answer_log / the five-round state.
    """
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    if req.kind not in ("cloze", "reading"):
        raise HTTPException(status_code=400, detail="不支持的题型")
    if req.source_type not in ("article", "question", "general"):
        raise HTTPException(status_code=400, detail="对话来源不合法")
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        if req.session_id:
            session = chat_service.get_session(conn, req.session_id, user["id"])
            if not session:
                raise HTTPException(status_code=404, detail="找不到这次对话")
            sid = req.session_id
            book = book_row(conn, session.get("book_id") or book["id"])
        else:
            context = chat_service.build_context(conn, req.source_type,
                                                 req.source_id, user["id"])
            if req.source_type in ("article", "question") and context is None:
                raise HTTPException(status_code=404, detail="找不到这篇内容或无权访问")
            sid = chat_service.start_session(conn, user["id"], req.source_type,
                                             req.source_id, context, book["id"])
            session = chat_service.get_session(conn, sid, user["id"])
        label = ("选词填空" if req.kind == "cloze" else "阅读理解")
        intent = f"出一篇{book['exam_type']}风格{label}题"
        try:
            card = chat_service.generate_practice_card(
                conn, user["id"], req.kind, book["id"], book)
            result = chat_service.send_card(conn, session, user["id"], intent, card)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except (GenerationFailed, NoProvider) as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "session_id": sid, "title": session["title"], **result}


@router.get("/api/chat/sessions")
def chat_sessions(user=Depends(current_user)):
    with transaction() as conn:
        rows = chat_service.list_sessions(conn, user["id"])
    return {"sessions": [{"id": r["id"], "title": r["title"],
                          "source_type": r["source_type"],
                          "source_id": r["source_id"],
                          "turn_count": r["turn_count"],
                          "updated_at": str(r["updated_at"])} for r in rows]}


@router.post("/api/chat/start")
def chat_start(req: ChatStartIn, user=Depends(current_user)):
    """Open a tutor session, optionally frozen to one article or question."""
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    if req.source_type not in ("article", "question", "general"):
        raise HTTPException(status_code=400, detail="对话来源不合法")
    with transaction() as conn:
        book = book_row(conn, user["book_id"])
        context = chat_service.build_context(conn, req.source_type, req.source_id,
                                             user["id"])
        if req.source_type in ("article", "question") and context is None:
            raise HTTPException(status_code=404, detail="找不到这篇内容或无权访问")
        sid = chat_service.start_session(conn, user["id"], req.source_type,
                                         req.source_id, context, book["id"])
        first = None
        if req.first_question.strip():
            session = chat_service.get_session(conn, sid, user["id"])
            try:
                first = chat_service.reply(conn, session, user["id"],
                                           req.first_question, book["id"], book)
            except (GenerationFailed, NoProvider) as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
    payload = {"ok": True, "session_id": sid,
               "title": (context.get("title") or context["label"]) if context else "通用英语"}
    if first:
        payload["reply"] = first
    return payload


@router.get("/api/chat/history")
def chat_history(session_id: int, user=Depends(current_user)):
    with transaction() as conn:
        messages = chat_service.history(conn, session_id, user["id"])
    if messages is None:
        raise HTTPException(status_code=404, detail="找不到这次对话")
    return {"session_id": session_id, "messages": messages}


@router.post("/api/chat/ask")
def chat_ask(req: ChatAskIn, user=Depends(current_user)):
    if not llm_configured():
        raise HTTPException(status_code=400, detail="请先配置 LLM_API_KEY 再用英语 AI")
    with transaction() as conn:
        session = chat_service.get_session(conn, req.session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="找不到这次对话")
        # Keep the shelf fixed to the session's book even if the user switches.
        book = book_row(conn, session.get("book_id") or user["book_id"])
        try:
            result = chat_service.reply(conn, session, user["id"], req.question,
                                        book["id"], book)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except (GenerationFailed, NoProvider) as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, **result}


@router.post("/api/chat/delete")
def chat_delete(req: ChatSessionIn, user=Depends(current_user)):
    with transaction() as conn:
        if not chat_service.delete_session(conn, req.session_id, user["id"]):
            raise HTTPException(status_code=404, detail="找不到这次对话")
    return {"ok": True}
