"""Request and response shapes for the HTTP API."""
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
    # extra="forbid": sending {"role": "admin"} on signup is rejected outright.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def lowercase(cls, v: str) -> str:
        return v.lower()

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("Password is too long (max 72 bytes).")
        return v


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    due_date: date | None
    due_time: time | None = None
    status: str
    created_at: datetime
    assigned_by: str | None = None  # set when an admin created this task for the user


class AdminTaskOut(TaskOut):
    owner: str
    created_by: str


class AdminUserOut(UserOut):
    task_count: int
    open_task_count: int


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ToolCallOut(BaseModel):
    tool: str
    arguments: dict | str
    ok: bool
    summary: str | None = None  # e.g. "marked 'be polite' done"


class ChatOut(BaseModel):
    reply: str
    tool_calls: list[ToolCallOut]
    error: bool = False


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    tool_calls: list[ToolCallOut] = []


TokenOut.model_rebuild()
