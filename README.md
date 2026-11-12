# TaskPilot

**An AI agent that manages tasks.** Users manage their to-do list by chatting. The agent decides which tool to call (add, list, update, delete, and assign for admins) and changes the data in the database. The task list sits next to the chat and highlights whatever the agent just changed.

## Live app and demo logins

| | |
|---|---|
| Frontend | `<VERCEL URL>` |
| Backend | `<RENDER URL>` (API docs at `<RENDER URL>/docs`) |
| Admin | username `<admin>`, password `<password>` |
| User | username `<user>`, password `<password>` |

The backend is on Render's free plan, so it sleeps after 15 minutes idle. The first request after that can take about a minute while it wakes up. The app shows a "can't reach the server, try again shortly" message in that case.

## Tech stack and why

| Part | Choice | Why |
|---|---|---|
| Backend | FastAPI (required), SQLAlchemy 2 | SQLAlchemy keeps queries in Python and makes it easy to put the owner check inside every query. |
| Database | Postgres on Neon | Free, hosted, survives restarts (Render's disk doesn't). |
| LLM | Groq, `openai/gpt-oss-120b`, low reasoning effort | Free tier and OpenAI-style tool calling. In testing, `qwen/qwen3.8-27b` often wrote malformed tool calls; gpt-oss-120b is the largest chat model available on Groq's free tier. Low reasoning effort is enough for picking from five tools and keeps replies fast. Model and effort are env vars. |
| Agent | Plain Python, no framework | The loop is about 100 lines, so a framework would add more to explain than it saves. |
| Auth | Own JWT (PyJWT) + bcrypt | Full control over how roles are enforced, and every line is explainable. |
| Validation | Pydantic | Validates both API requests and the LLM's tool arguments. Tool schemas are generated from the same models, so what the LLM sees and what gets validated can't drift apart. |
| Frontend | React (Vite) + React Router | Plain single-page app, no server rendering needed. Builds to static files for Vercel. |
| Hosting | Vercel (frontend), Render (backend), Neon (DB) | All free with no credit card. |

## How the AI part works

`backend/app/agent/loop.py` runs a standard tool-calling loop:

1. Build the messages: a system prompt (today's date, plus the user's pending tasks with their ids), the user's last 10 chat messages from the DB, and the new message. Because the tasks are already in the prompt, the model can usually act straight away without a `list_tasks` round first.
2. Send them to the LLM along with the tools this user is allowed to use.
3. If the LLM asks for tool calls, the backend runs each one and adds the results to the conversation, then asks the LLM again.
4. When the LLM answers in plain text, **its claims are checked against what actually ran.** If the reply says a task was added, updated or deleted but no matching tool call succeeded in this turn, the model is told so and gets one chance to call the tool or correct itself. If it still doesn't match, the reply is replaced with an honest message and is not saved to history, so a false claim can't mislead later turns.
5. It stops early if the model repeats a tool call that already failed, and after 6 rounds at most.

**The model never does date maths.** It passes dates and times the way the user said them ("tomorrow", "friday", "17 june", "5pm", "9:30 am") and the backend converts them. A day and month with no year means the next time that date comes around. A time with no date means today if it's still ahead, otherwise tomorrow. A bare hour like "at 5" is rejected so the agent asks am or pm. Tool results include a ready-made label like "Thu 17 Jun 2027", which the model copies, because models often get weekdays wrong.

**The agent can't misremember what it did.** Each saved reply stores the tool calls that actually ran in that turn, and when old messages are sent back to the model they carry a note like "[actions actually taken: marked 'be polite' done (id 12)]". The prompt also lists recently completed tasks, not just pending ones. So if a user asks "why did you mark that done?", the model answers from the record instead of guessing, and it's told to admit mistakes and offer to undo them.

**Acknowledgements can't change anything.** If the message is just "ok", "thanks" or "lol" and the assistant's last message wasn't a question, the model is only offered the read-only `list_tasks` tool, and the backend rejects any write call it tries anyway. After a question like "want me to mark it done?", "ok" still works as a yes.

**Guards in the tools themselves:** a due date in the past is refused until the user confirms it, and adding a task whose title matches an existing pending task is refused with a hint to update that task instead. This stops corrections like "I said June" from creating duplicates.

This is what makes multi-step requests work. "Mark the report done and add a task to start the next one" becomes `list_tasks` (to find the report's id), then `update_task` plus `add_task`, then a reply. The UI shows which tools ran under each reply, and failed calls are marked.

The LLM only proposes actions. The backend decides whether they're allowed (see below). LLM errors become friendly messages instead of crashes. Rate limits are reported immediately with the wait time Groq gives, rather than stalling the request for up to 20 seconds. If the model writes a malformed tool call, the error is sent back to it and it retries (up to twice). Feeding back the error matters: retrying the same messages at temperature 0 just reproduces the same broken call. Every rejected tool call is logged with its arguments.

## How login and roles work

- Sign up and log in with a username and password. Passwords are hashed with bcrypt.
- Login returns a JWT that contains only the user id, signed with a secret from env vars, and expires after 60 minutes.
- **The role is read from the database on every request**, not from the token, so a forged or old token can't claim admin.
- Signup always creates a `user`. Sending `"role": "admin"` is rejected. Admins can only be created with `scripts/seed.py`.
- Admin API routes (`/admin/*`) check the role on the backend and return 403 to normal users, even if they call the API directly.
- The frontend only shows the Admin page and link to admins, but that's for convenience; the backend is what enforces it.

**Keeping the agent safe ("the backend, not the LLM"):**

- No tool takes a user or owner argument. The current user always comes from the token, never from the model.
- Every query includes the owner check (e.g. delete where id = X **and** owner = current user), so the check and the action are one step. Another user's task just looks "not found."
- Each user only gets the tools for their role. If the model calls a tool it wasn't given, it's rejected, and `assign_task` also re-checks for admin inside the function.
- All tool arguments are validated with Pydantic before running, and unknown fields like `owner_id` are rejected.
- The system prompt tells the model to treat task titles as data, not instructions.

So even if someone tells the agent "delete everyone else's tasks," nothing outside their own tasks can be touched. `tests/test_api.py` checks exactly this.

## Extra features

- **Live task list beside the chat**, which highlights the rows the agent just added or changed. Tasks can also be ticked off or deleted by hand, with the same rule as the agent: only your own tasks, admins included.
- **Tool trail under each reply**, showing which actions ran and which failed.
- **Claim verification**: the agent can't tell you it did something unless the matching tool call actually succeeded.
- **Natural dates and times handled in code** ("tomorrow at 5pm", "next friday", "17 june 9:30am"), with correct weekdays in replies. Timed tasks turn overdue at that minute; date-only tasks the next day.
- **Duplicate and past-date guards**, so corrections update the existing task instead of adding copies.
- **Chat history saved in the database**, per user, with a Clear chat button. It survives refreshes and works across devices, including the tool chips under each reply.
- **`assign_task` for admins**: "Assign ravi a task to review the deck by Monday." Assigned tasks show a "from <admin>" badge in the user's list, and the agent tells the user when a task came from an admin.
- **Admin overview page**: all users with task counts, and all tasks with owner and creator.
- **Overdue and due-today dates** are flagged in the task list.
- **Automated tests** (`pytest`) for auth, roles, cross-user attacks, multi-step requests, fake-claim correction, malformed-call retries, date and time parsing, the startup column migration, the acknowledgement guard, action notes in history, duplicate and past-date guards, repeated-failure stopping, rate limits, the iteration cap and LLM outages. They use a scripted fake LLM, so they run offline.
- Dark mode and a mobile layout.

## Run it locally

You need Python 3.12, Node 18+, a Postgres URL (Neon free tier works) and a Groq API key.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values
python -m scripts.seed        # creates the admin and demo user from .env
uvicorn app.main:app --reload # http://localhost:8000/docs

# Tests
pip install -r requirements-dev.txt
pytest

# Frontend (new terminal)
cd frontend
cp .env.example .env          # VITE_API_URL=http://localhost:8000
npm install
npm run dev                   # http://localhost:5173
```

## Deploying

1. **Neon:** create a project and copy the connection string.
2. **Render:** New > Web Service from this repo, root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Add the env vars from `backend/.env.example`. (`render.yaml` has the same settings.)
3. **Seed the demo accounts:** run `python -m scripts.seed` locally with `DATABASE_URL` pointing at Neon.
4. **Vercel:** import the repo, root directory `frontend`, set `VITE_API_URL` to the Render URL.
5. **Back on Render:** set `CORS_ORIGINS` to the Vercel URL (no trailing slash) and redeploy.

## Not finished / could be improved

- **No confirmation before deletes.** The agent deletes as soon as the user asks. A confirm step for deletes and bulk changes would be safer.
- **No database-level row security.** Scoping is enforced in the backend code, not by Postgres Row Level Security, which would be a last line of defence if the code had a bug.
- **No tool-call audit log.** Calls are shown in the UI but not stored.
- **Tables are created on startup** instead of with migrations (Alembic). Columns added later (`due_time`, `updated_at`, `actions`) are added by a small check on startup, which works but doesn't scale to many schema changes.
- **The token is stored in localStorage**, which is simple but readable by any script on the page. An httpOnly cookie would be safer.
- **No login rate limiting.**
- **The acknowledgement guard is a word list**, so an unusual "ok"-type message could slip past it. The prompt rule is the second line of defence.
- **The claim check uses simple patterns** ("added", "deleted", "marked"…), so an unusual phrasing could slip past it. Asking the model for structured output alongside the reply would be stricter.
- **No eval set against the real LLM.** The tests use a fake LLM to check our code; a set of real prompts with expected tool calls would measure the model's tool choice.
