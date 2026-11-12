import json

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.loop import run_agent
from app.db import get_db
from app.models import ChatMessage, User
from app.schemas import ChatIn, ChatMessageOut, ChatOut
from app.security import get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatOut)
def chat(body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = run_agent(user, db, body.message.strip())
    return ChatOut(reply=result.reply, tool_calls=result.tool_calls, error=result.error)


@router.get("/history", response_model=list[ChatMessageOut])
def history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.user_id == user.id).order_by(ChatMessage.id.desc()).limit(100)
    ).all()
    return [
        ChatMessageOut(
            id=m.id, role=m.role, content=m.content, created_at=m.created_at,
            tool_calls=json.loads(m.actions) if m.actions else [],  # chips survive a refresh
        )
        for m in reversed(rows)
    ]


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id))
    db.commit()
