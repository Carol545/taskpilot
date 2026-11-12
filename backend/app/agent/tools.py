"""
TaskPilot agent tools.

Safety rules this file enforces (layers 2-4):
  * No tool takes a user/owner argument. `current_user` is always passed in by
    our code (from the JWT + DB lookup), never by the LLM.
  * Ownership is part of every query (WHERE id = ? AND owner_id = ?), so the
    check and the action happen in one atomic statement.
  * The tool list is built per role. A tool not in the caller's list is
    rejected even if the model names it.
  * Every argument is validated with Pydantic before anything runs.
    extra="forbid" means the model can't sneak in fields like owner_id.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, time
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, WithJsonSchema, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, aliased

from app.agent.dates import default_date_for, is_past, label, parse_due, parse_time, split_time, today
from app.models import Task, User

log = logging.getLogger(__name__)

MAX_LIST_RESULTS = 50


# ---------------------------------------------------------------------------
# Argument models (one per tool). These are also the JSON schemas the LLM sees.
# ---------------------------------------------------------------------------

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# The model passes dates the way the user said them; parse_due() does the calendar maths.
DueDate = Annotated[
    date | None,
    BeforeValidator(parse_due),
    WithJsonSchema({
        "type": "string",
        "description": "Due date exactly as the user said it, e.g. 'tomorrow', 'friday', '17 june', "
                       "or YYYY-MM-DD. Omit if the user gave no date. Don't convert it yourself.",
    }),
]
DueTime = Annotated[
    time | None,
    BeforeValidator(parse_time),
    WithJsonSchema({
        "type": "string",
        "description": "Time exactly as the user said it, e.g. '5pm', '9:30 am', '17:30', 'noon'. "
                       "Omit if no time was given. Don't convert it yourself.",
    }),
]


class _DueArgs(_Args):
    """Base for tools that take a due date and time."""

    @model_validator(mode="before")
    @classmethod
    def _move_time_out_of_date(cls, data: Any) -> Any:
        # If the model put the time inside due_date ("tomorrow at 5pm"), move it to due_time.
        if isinstance(data, dict) and isinstance(data.get("due_date"), str):
            rest, t = split_time(data["due_date"])
            if t is not None:
                data = {**data, "due_date": rest or None}
                if not data.get("due_time"):
                    data["due_time"] = t
        return data


ALLOW_PAST = Field(False, description="Set true only after the user confirms they really want a due date in the past.")


class AddTaskArgs(_DueArgs):
    title: str = Field(min_length=1, max_length=200, description="Short task title, e.g. 'Submit monthly report'.")
    due_date: DueDate = None
    due_time: DueTime = None
    allow_past_date: bool = ALLOW_PAST
    allow_duplicate: bool = Field(False, description="Set true only if the user explicitly wants a second task with the same title.")


class ListTasksArgs(_Args):
    status: Literal["pending", "done"] | None = Field(None, description="Filter by status. Omit for all tasks.")
    title_contains: str | None = Field(None, max_length=100, description="Case-insensitive text to search for in titles. Use this to find a task the user mentions by name.")
    due_on_or_after: DueDate = None
    due_on_or_before: DueDate = None


class UpdateTaskArgs(_DueArgs):
    task_id: int = Field(gt=0, description="ID of the task, taken from list_tasks results. Never guess it.")
    title: str | None = Field(None, min_length=1, max_length=200, description="New title.")
    due_date: DueDate = None
    due_time: DueTime = None
    clear_due_time: bool = Field(False, description="Set true to remove the task's time and keep only the date.")
    status: Literal["pending", "done"] | None = Field(None, description="Set to 'done' to mark the task complete.")
    allow_past_date: bool = ALLOW_PAST

    @model_validator(mode="after")
    def at_least_one_change(self):
        if (self.title is None and self.due_date is None and self.due_time is None
                and self.status is None and not self.clear_due_time):
            raise ValueError("Provide at least one of title, due_date, due_time, clear_due_time or status to change.")
        return self


class DeleteTaskArgs(_Args):
    task_id: int = Field(gt=0, description="ID of the task, taken from list_tasks results. Never guess it.")


class AssignTaskArgs(_DueArgs):
    username: str = Field(min_length=1, max_length=50, description="Username of the person to give the task to.")
    title: str = Field(min_length=1, max_length=200, description="Short task title.")
    due_date: DueDate = None
    due_time: DueTime = None
    allow_past_date: bool = ALLOW_PAST


# ---------------------------------------------------------------------------
# Tool functions. Signature is always (args, current_user, db) -> dict.
# ---------------------------------------------------------------------------

def _task_to_dict(t: Task, assigned_by: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": t.id, "title": t.title, "status": t.status, "due_date": None}
    if assigned_by:
        out["assigned_by"] = assigned_by  # an admin gave the user this task
    if t.due_date:
        out["due_date"] = t.due_date.isoformat()
        if t.due_time:
            out["due_time"] = t.due_time.strftime("%H:%M")
        out["due_label"] = label(t.due_date, t.due_time)  # copy this into replies, don't compute weekdays
        out["overdue"] = t.status == "pending" and is_past(t.due_date, t.due_time)
    return out


def _past_date_error(due: date | None, allowed: bool, at: time | None = None) -> dict | None:
    if due and is_past(due, at) and not allowed:
        return {
            "error": f"The due date {label(due, at)} is in the past (today is {label(today())}). "
                     "Ask the user to confirm the date. If they really want it, call again with allow_past_date=true."
        }
    return None


def add_task(args: AddTaskArgs, current_user: User, db: Session) -> dict:
    if args.due_time and not args.due_date:
        args.due_date = default_date_for(args.due_time)
    if err := _past_date_error(args.due_date, args.allow_past_date, args.due_time):
        return err
    if not args.allow_duplicate:
        existing = db.scalars(
            select(Task).where(
                Task.owner_id == current_user.id,
                Task.status == "pending",
                func.lower(Task.title) == args.title.lower(),
            )
        ).first()
        if existing:
            return {
                "error": "A pending task with this title already exists.",
                "existing_task": _task_to_dict(existing),
                "hint": "If the user is correcting or changing it, use update_task on that id. "
                        "Only add a second copy if they explicitly ask, with allow_duplicate=true.",
            }
    task = Task(
        title=args.title,
        due_date=args.due_date,
        due_time=args.due_time,
        owner_id=current_user.id,
        created_by_id=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"added": _task_to_dict(task)}


def list_tasks(args: ListTasksArgs, current_user: User, db: Session) -> dict:
    creator = aliased(User)
    stmt = (
        select(Task, creator.username)
        .join(creator, Task.created_by_id == creator.id)
        .where(Task.owner_id == current_user.id)  # scoping
    )
    if args.status:
        stmt = stmt.where(Task.status == args.status)
    if args.title_contains:
        stmt = stmt.where(Task.title.ilike(f"%{args.title_contains}%"))
    if args.due_on_or_after:
        stmt = stmt.where(Task.due_date >= args.due_on_or_after)
    if args.due_on_or_before:
        stmt = stmt.where(Task.due_date <= args.due_on_or_before)
    stmt = stmt.order_by(Task.due_date.is_(None), Task.due_date, Task.due_time.is_(None), Task.due_time, Task.id).limit(MAX_LIST_RESULTS)

    rows = db.execute(stmt).all()
    tasks = [_task_to_dict(t, name if t.created_by_id != t.owner_id else None) for t, name in rows]
    return {"count": len(tasks), "tasks": tasks}


def update_task(args: UpdateTaskArgs, current_user: User, db: Session) -> dict:
    changes = args.model_dump(exclude={"task_id", "allow_past_date", "clear_due_time"}, exclude_none=True)
    if args.clear_due_time:
        changes["due_time"] = None
    if args.due_time or args.due_date:
        # Need the current date/time to check the combined result isn't in the past.
        current = db.scalars(
            select(Task).where(Task.id == args.task_id, Task.owner_id == current_user.id)  # scoping
        ).one_or_none()
        if current is None:
            return {"error": f"Task {args.task_id} not found."}
        new_date = args.due_date or current.due_date
        new_time = None if args.clear_due_time else (args.due_time or current.due_time)
        if new_time and not new_date:
            new_date = changes["due_date"] = default_date_for(new_time)
        if err := _past_date_error(new_date, args.allow_past_date, new_time):
            return err
    stmt = (
        update(Task)
        .where(Task.id == args.task_id, Task.owner_id == current_user.id)  # scoping, atomic
        .values(**changes)
        .returning(Task)
    )
    task = db.scalars(stmt).one_or_none()
    if task is None:
        db.rollback()
        # Same message whether it doesn't exist or belongs to someone else.
        return {"error": f"Task {args.task_id} not found."}
    db.commit()
    return {"updated": _task_to_dict(task)}


def delete_task(args: DeleteTaskArgs, current_user: User, db: Session) -> dict:
    stmt = (
        delete(Task)
        .where(Task.id == args.task_id, Task.owner_id == current_user.id)  # scoping, atomic
        .returning(Task.id, Task.title)
    )
    row = db.execute(stmt).one_or_none()
    if row is None:
        db.rollback()
        return {"error": f"Task {args.task_id} not found."}
    db.commit()
    return {"deleted": {"id": row.id, "title": row.title}}


def assign_task(args: AssignTaskArgs, current_user: User, db: Session) -> dict:
    # Re-check inside the function, even though only admins are offered this tool.
    if current_user.role != "admin":
        return {"error": "Only admins can assign tasks."}

    if args.due_time and not args.due_date:
        args.due_date = default_date_for(args.due_time)
    if err := _past_date_error(args.due_date, args.allow_past_date, args.due_time):
        return err
    target = db.scalars(select(User).where(User.username == args.username.lower())).one_or_none()
    if target is None:
        return {"error": f"No user named '{args.username}'."}

    task = Task(
        title=args.title,
        due_date=args.due_date,
        due_time=args.due_time,
        owner_id=target.id,
        created_by_id=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"assigned_to": target.username, "task": _task_to_dict(task)}


# ---------------------------------------------------------------------------
# Registry: which tools exist, and which roles get them.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[_Args]
    fn: Callable[[Any, User, Session], dict]
    admin_only: bool = False

    def schema(self) -> dict:
        """OpenAI-style tool schema (Groq uses the same format)."""
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }


TOOLS: list[ToolSpec] = [
    ToolSpec(
        "add_task",
        "Create a new task for the current user.",
        AddTaskArgs, add_task,
    ),
    ToolSpec(
        "list_tasks",
        "List the current user's tasks, with optional filters. Also use this to find a task's id "
        "before updating or deleting it.",
        ListTasksArgs, list_tasks,
    ),
    ToolSpec(
        "update_task",
        "Change a task's title, due date or status. Use status='done' to mark it complete.",
        UpdateTaskArgs, update_task,
    ),
    ToolSpec(
        "delete_task",
        "Permanently delete a task. Only use when the user explicitly asks to delete or remove it, "
        "not when they say it is finished.",
        DeleteTaskArgs, delete_task,
    ),
    ToolSpec(
        "assign_task",
        "Admin only: create a task for another user, by username.",
        AssignTaskArgs, assign_task, admin_only=True,
    ),
]


def tools_for(user: User) -> dict[str, ToolSpec]:
    """The only tools this user may call. Built per request from the DB role."""
    is_admin = user.role == "admin"
    return {t.name: t for t in TOOLS if is_admin or not t.admin_only}


def tool_schemas_for(user: User) -> list[dict]:
    """What gets sent to the LLM in the `tools` parameter."""
    return [t.schema() for t in tools_for(user).values()]


def execute_tool(name: str, raw_args: str | None, current_user: User, db: Session,
                 offered: set[str] | None = None) -> dict:
    """
    Run one tool call from the LLM. Never raises: every failure becomes an
    {"error": ...} dict that goes back to the model as the tool result.
    """
    if offered is not None and name not in offered:
        # The model called a tool it wasn't offered this turn (e.g. a write after "ok thanks").
        result = {"error": f"'{name}' isn't available for this message. Only change tasks when the user asks."}
    else:
        result = _execute(name, raw_args, current_user, db)
    if "error" in result:
        # Every rejected call is logged, so failures are never invisible.
        log.warning("Tool %s rejected for user %s: %s | args=%s", name, current_user.id,
                    result.get("problems") or result["error"], (raw_args or "")[:300])
    return result


def _execute(name: str, raw_args: str | None, current_user: User, db: Session) -> dict:
    allowed = tools_for(current_user)
    spec = allowed.get(name)
    if spec is None:
        return {"error": f"Unknown tool '{name}'."}

    try:
        payload = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON."}

    try:
        args = spec.args_model.model_validate(payload)
    except ValidationError as e:
        problems = [f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors()]
        return {"error": "Invalid arguments.", "problems": problems}

    try:
        return spec.fn(args, current_user, db)
    except Exception:
        db.rollback()
        log.exception("Tool %s failed for user %s", name, current_user.id)
        return {"error": "Something went wrong running that action. Try again."}
