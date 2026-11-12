import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from sqlalchemy import inspect, text

from app.db import engine
from app.models import Base
from app.routers import admin, auth, chat, tasks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("taskpilot")


# Columns added after the first deploy. create_all() only creates missing tables, not
# missing columns, so existing databases get them here. (Alembic would do this properly.)
_ADDED_COLUMNS = {
    "tasks": {"due_time": "TIME", "updated_at": "TIMESTAMP"},
    "chat_messages": {"actions": "TEXT"},
}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    for table, columns in _ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue  # create_all() makes new tables with every column already
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, sql_type in columns.items():
            if name not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                log.info("Added column %s.%s", table, name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates missing tables on startup. Fine for this size of project; a real
    # app would use Alembic migrations instead.
    Base.metadata.create_all(engine)
    _add_missing_columns()
    log.info("Using Groq model %s (reasoning_effort=%s)", settings.groq_model, settings.groq_reasoning_effort)
    yield


app = FastAPI(
    title="TaskPilot API",
    description="Chat with an AI agent to manage your tasks. Log in via /auth/login, "
                "then click Authorize and paste the access_token.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Try again."})


@app.get("/", tags=["health"])
def health():
    return {"status": "ok", "service": "TaskPilot API"}


app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(chat.router)
app.include_router(admin.router)
