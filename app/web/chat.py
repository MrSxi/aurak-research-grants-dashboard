import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import config
from app.auth import require
from app.db import SessionLocal, get_db
from app.models import Grant
from app.services import chat as chat_service, llm
from app.web.common import render

router = APIRouter()


@router.get("/chat")
def chat_page(request: Request, grant_id: int | None = None, db: Session = Depends(get_db)):
    user = require(request, db)
    g = db.get(Grant, grant_id) if grant_id else None
    provider = llm.active_provider()
    return render(request, db, user, "chat.html", active="/chat", ctx_grant=g, available=provider is not None,
                  model=config.LLM_MODEL, model_desc=llm.describe(provider or "ollama"),
                  unavailable=chat_service.UNAVAILABLE)


@router.post("/api/chat")
async def chat_api(request: Request, db: Session = Depends(get_db)):
    user = require(request, db)
    body = await request.json()
    question = (body.get("question") or "").strip()[:2000]
    if not question:
        return JSONResponse({"answer": "Please type a question.", "sources": []})
    gid = body.get("grant_id")
    history, user_id = body.get("history") or [], user.id

    def events():
        # the stream outlives the request's session, so it uses its own
        s = SessionLocal()
        try:
            for e in chat_service.stream_answer(s, question, history, user_id, int(gid) if gid else None):
                yield json.dumps(e) + "\n"
        finally:
            s.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")
