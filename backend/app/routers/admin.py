"""Admin-only routes. require_admin checks the role from the DB on every call,
so a normal user calling these directly gets a 403."""
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.db import get_db
from app.models import Task, User
from app.schemas import AdminTaskOut, AdminUserOut
from app.security import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users", response_model=list[AdminUserOut])
def all_users(db: Session = Depends(get_db)):
    stmt = (
        select(
            User.id,
            User.username,
            User.role,
            func.count(Task.id).label("task_count"),
            func.coalesce(func.sum(case((Task.status == "pending", 1), else_=0)), 0).label("open_task_count"),
        )
        .outerjoin(Task, Task.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.username)
    )
    return [AdminUserOut(**row._mapping) for row in db.execute(stmt)]


@router.get("/tasks", response_model=list[AdminTaskOut])
def all_tasks(db: Session = Depends(get_db)):
    owner = aliased(User)
    creator = aliased(User)
    stmt = (
        select(Task, owner.username, creator.username)
        .join(owner, Task.owner_id == owner.id)
        .join(creator, Task.created_by_id == creator.id)
        .order_by(owner.username, Task.status, Task.due_date.is_(None), Task.due_date, Task.due_time)
    )
    return [
        AdminTaskOut(
            id=t.id, title=t.title, due_date=t.due_date, due_time=t.due_time, status=t.status,
            created_at=t.created_at, owner=o, created_by=c,
        )
        for t, o, c in db.execute(stmt)
    ]
