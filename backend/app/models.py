"""Database models. Kept minimal: the agent tools only need these fields."""
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, Time, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Always "user" on signup. Admins are promoted by hand in the DB.
    role: Mapped[str] = mapped_column(String(10), default="user")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # optional; date is set whenever time is
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Who created it: same as owner normally, the admin when assigned.
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Set by SQLAlchemy on insert and on every update (so we can show recently completed tasks).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=True)


class ChatMessage(Base):
    """Cross-turn chat history: user text, final assistant replies, and a record of
    which tool calls actually ran in each turn."""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # For assistant messages: JSON list of the tool calls that actually ran in that turn.
    # Fed back to the model with the history so it can't misremember what it did.
    actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
