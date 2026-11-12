from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status as http_status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, aliased

from app.db import get_db
from app.models import Task, User
from app.schemas import TaskOut
from app.security import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def my_tasks(
    status: Literal["pending", "done"] | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    creator = aliased(User)
    stmt = (
        select(Task, creator.username)
        .join(creator, Task.created_by_id == creator.id)
        .where(Task.owner_id == user.id)  # only your own tasks
    )
    if status:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.order_by(Task.status, Task.due_date.is_(None), Task.due_date, Task.due_time.is_(None), Task.due_time, Task.id)
    return [
        TaskOut(
            id=t.id, title=t.title, due_date=t.due_date, due_time=t.due_time, status=t.status, created_at=t.created_at,
            assigned_by=creator_name if t.created_by_id != t.owner_id else None,
        )
        for t, creator_name in db.execute(stmt)
    ]


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "done"]


# Manual tick-off and delete from the task list. Same rule as the agent's tools:
# the owner check is inside the query, so you can only change your own tasks
# (admins included), and someone else's task just looks "not found".

@router.patch("/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def set_status(task_id: int, body: StatusIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = db.execute(
        update(Task).where(Task.id == task_id, Task.owner_id == user.id).values(status=body.status)
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Task not found.")
    db.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@router.delete("/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def remove(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = db.execute(delete(Task).where(Task.id == task_id, Task.owner_id == user.id))
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Task not found.")
    db.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
