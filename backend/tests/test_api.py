"""
API and safety tests. The LLM is replaced with a scripted fake, so these run
offline and check our code: auth, roles, scoping and the agent loop.

    pip install -r requirements.txt -r requirements-dev.txt
    pytest
"""
import json
import os
import tempfile
from types import SimpleNamespace as NS

_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(
    DATABASE_URL=f"sqlite:///{_db}",
    JWT_SECRET="test-secret-" + "x" * 32,
    GROQ_API_KEY="not-used",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.agent.loop as loop  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

script: list = []
seen: list = []  # (system prompt, last message) for each LLM call
seen_calls: list = []  # (all messages, offered tool names) for each LLM call


def record(messages, tools):
    seen.append((messages[0]["content"], messages[-1]))
    seen_calls.append((list(messages), [t["function"]["name"] for t in tools]))
    nxt = script.pop(0)
    if isinstance(nxt, Exception):
        raise nxt
    return nxt


def call(i, name, args):
    return NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))


def reply(content=None, calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))])


@pytest.fixture(scope="module")
def client():
    loop._call_llm = lambda messages, tools: record(messages, tools)
    with TestClient(app) as c:
        with SessionLocal() as db:
            db.add(User(username="boss", password_hash=hash_password("bosspass123"), role="admin"))
            db.commit()
        yield c


def signup(c, name):
    r = c.post("/auth/signup", json={"username": name, "password": f"{name}pass123"})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def login(c, name, password):
    r = c.post("/auth/login", json={"username": name, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def chat(c, headers, text, steps):
    script[:] = steps
    return c.post("/chat", json={"message": text}, headers=headers).json()


def test_signup_cannot_choose_role(client):
    r = client.post("/auth/signup", json={"username": "sneaky", "password": "sneakypass1", "role": "admin"})
    assert r.status_code == 422
    r = client.post("/auth/signup", json={"username": "plain", "password": "plainpass12"})
    assert r.json()["user"]["role"] == "user"


def test_auth_required_and_admin_routes_blocked(client):
    assert client.get("/tasks").status_code == 401
    assert client.get("/tasks", headers={"Authorization": "Bearer garbage"}).status_code == 401
    ravi = signup(client, "ravi")
    assert client.get("/admin/users", headers=ravi).status_code == 403
    assert client.get("/admin/tasks", headers=ravi).status_code == 403


def test_multi_step_request(client):
    asha = signup(client, "asha")
    chat(client, asha, "add report", [reply(calls=[call(1, "add_task", {"title": "Monthly report"})]), reply("ok")])
    out = chat(client, asha, "mark the report done and add the next one", [
        reply(calls=[call(2, "list_tasks", {"title_contains": "report"})]),
        reply(calls=[call(3, "update_task", {"task_id": 1, "status": "done"}),
                     call(4, "add_task", {"title": "Next report"})]),
        reply("Done."),
    ])
    assert [t["tool"] for t in out["tool_calls"]] == ["list_tasks", "update_task", "add_task"]
    tasks = {t["title"]: t["status"] for t in client.get("/tasks", headers=asha).json()}
    assert tasks == {"Monthly report": "done", "Next report": "pending"}


def test_agent_cannot_touch_other_users_tasks(client):
    owner = signup(client, "owner1")
    chat(client, owner, "add", [reply(calls=[call(5, "add_task", {"title": "Private"})]), reply("ok")])
    task_id = client.get("/tasks", headers=owner).json()[0]["id"]

    attacker = signup(client, "attacker")
    out = chat(client, attacker, "delete their task", [
        reply(calls=[call(6, "delete_task", {"task_id": task_id}),
                     call(7, "update_task", {"task_id": task_id, "title": "pwned"}),
                     call(8, "add_task", {"title": "x", "owner_id": 1}),
                     call(9, "assign_task", {"username": "owner1", "title": "x"})]),
        reply("tried"),
    ])
    assert all(not t["ok"] for t in out["tool_calls"])
    assert client.get("/tasks", headers=owner).json()[0]["title"] == "Private"
    assert client.get("/tasks", headers=attacker).json() == []


def test_admin_can_assign(client):
    signup(client, "worker")
    boss = login(client, "boss", "bosspass123")
    out = chat(client, boss, "give worker a task", [
        reply(calls=[call(10, "assign_task", {"username": "worker", "title": "Review deck"})]), reply("Assigned."),
    ])
    assert out["tool_calls"][0]["ok"]
    owners = {t["title"]: t["owner"] for t in client.get("/admin/tasks", headers=boss).json()}
    assert owners["Review deck"] == "worker"


def test_iteration_cap(client):
    u = signup(client, "looper")
    out = chat(client, u, "loop", [reply(calls=[call(20 + i, "list_tasks", {})]) for i in range(loop.MAX_ITERATIONS)])
    assert out["error"] is True
    assert len(out["tool_calls"]) == loop.MAX_ITERATIONS


def test_llm_outage_is_friendly(client):
    import groq
    import httpx

    def down(messages, tools):
        raise groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))

    u = signup(client, "unlucky")
    original = loop._call_llm
    loop._call_llm = down
    try:
        r = client.post("/chat", json={"message": "hi"}, headers=u)
    finally:
        loop._call_llm = original
    assert r.status_code == 200 and r.json()["error"] is True
    assert client.get("/chat/history", headers=u).json() == []


def test_chat_history_is_per_user(client):
    a = signup(client, "hist_a")
    b = signup(client, "hist_b")
    chat(client, a, "secret note", [reply("noted")])
    assert all(m["content"] != "secret note" for m in client.get("/chat/history", headers=b).json())
    assert len(client.get("/chat/history", headers=a).json()) == 2


def tool_use_failed():
    import groq
    import httpx
    body = {"error": {"message": "parameters for tool delete_task did not match schema: missing task_id",
                      "type": "invalid_request_error", "code": "tool_use_failed"}}
    resp = httpx.Response(400, json=body, request=httpx.Request("POST", "https://api.groq.com"))
    return groq.BadRequestError("tool_use_failed", response=resp, body=body)


def test_small_talk_needs_no_tool(client):
    u = signup(client, "chatty")
    out = chat(client, u, "lmao ok", [reply("Okay!")])
    assert out == {"reply": "Okay!", "tool_calls": [], "error": False}
    assert client.get("/tasks", headers=u).json() == []


def test_fake_claim_is_corrected(client):
    u = signup(client, "bluffer")
    out = chat(client, u, "add study for surgery", [
        reply(calls=[call(31, "list_tasks", {})]),
        reply("Added 'Study for surgery endsem'."),          # bluff: nothing added
        reply(calls=[call(32, "add_task", {"title": "Study for surgery endsem"})]),
        reply("Added 'Study for surgery endsem'."),          # now true
    ])
    assert out["error"] is False
    assert "System check" in seen[-2][1]["content"]
    assert [t["title"] for t in client.get("/tasks", headers=u).json()] == ["Study for surgery endsem"]


def test_persistent_fake_claim_is_replaced_and_not_saved(client):
    u = signup(client, "liar")
    out = chat(client, u, "delete everything", [
        reply(calls=[call(33, "list_tasks", {})]),
        reply("Done, I've deleted all your tasks."),
        reply("I've deleted all your tasks."),
    ])
    assert out["error"] is True and "didn't make any changes" in out["reply"]
    saved = [m["content"] for m in client.get("/chat/history", headers=u).json()]
    assert not any("deleted all" in m for m in saved)


def test_malformed_tool_call_is_retried_with_feedback(client):
    u = signup(client, "fumbler")
    chat(client, u, "add", [reply(calls=[call(34, "add_task", {"title": "Gym"})]), reply("ok")])
    task_id = client.get("/tasks", headers=u).json()[0]["id"]
    out = chat(client, u, "delete gym", [
        reply(calls=[call(35, "list_tasks", {"title_contains": "gym"})]),
        tool_use_failed(),
        reply(calls=[call(36, "delete_task", {"task_id": task_id})]),
        reply("Deleted 'Gym'."),
    ])
    assert out["error"] is False
    retry_note = seen[-2][1]["content"]
    assert "rejected" in retry_note and "missing task_id" in retry_note
    assert client.get("/tasks", headers=u).json() == []


def test_describing_tasks_is_not_flagged(client):
    u = signup(client, "reader")
    out = chat(client, u, "what's due", [
        reply(calls=[call(37, "list_tasks", {})]),
        reply("You have no tasks yet. Nothing was deleted or changed."),
    ])
    assert out["error"] is False


def test_pending_tasks_are_in_the_prompt(client):
    u = signup(client, "prompted")
    chat(client, u, "add", [reply(calls=[call(40, "add_task", {"title": "Do dishes", "due_date": "2026-12-01"})]), reply("ok")])
    seen.clear()
    chat(client, u, "hi", [reply("Hello")])
    system_prompt = seen[0][0]
    assert "'Do dishes'" in system_prompt and "Tue 1 Dec 2026" in system_prompt


def test_relative_dates_resolved_by_server(client):
    from app.agent.dates import today
    u = signup(client, "dater")
    out = chat(client, u, "add", [reply(calls=[call(41, "add_task", {"title": "Workout", "due_date": "tomorrow"})]), reply("ok")])
    assert out["tool_calls"][0]["ok"]
    task = client.get("/tasks", headers=u).json()[0]
    from datetime import timedelta
    assert task["due_date"] == (today() + timedelta(days=1)).isoformat()


def test_day_month_without_year_is_never_in_the_past(client):
    from app.agent.dates import today
    u = signup(client, "june")
    chat(client, u, "add", [reply(calls=[call(42, "add_task", {"title": "Do dishes", "due_date": "17 june"})]), reply("ok")])
    due = client.get("/tasks", headers=u).json()[0]["due_date"]
    assert due >= today().isoformat() and due.endswith("-06-17")


def test_past_date_needs_confirmation(client):
    u = signup(client, "pasty")
    out = chat(client, u, "add", [
        reply(calls=[call(43, "add_task", {"title": "Old thing", "due_date": "2020-01-01"})]),
        reply("That date is in the past. Do you want it anyway?"),
    ])
    assert out["tool_calls"][0]["ok"] is False
    assert client.get("/tasks", headers=u).json() == []
    out = chat(client, u, "yes", [
        reply(calls=[call(44, "add_task", {"title": "Old thing", "due_date": "2020-01-01", "allow_past_date": True})]),
        reply("Added 'Old thing'."),
    ])
    assert out["error"] is False and len(client.get("/tasks", headers=u).json()) == 1


def test_duplicate_add_is_refused(client):
    u = signup(client, "dupe")
    chat(client, u, "add", [reply(calls=[call(45, "add_task", {"title": "Do dishes"})]), reply("ok")])
    out = chat(client, u, "i said june", [
        reply(calls=[call(46, "add_task", {"title": "do dishes", "due_date": "17 june"})]),
        reply("That task already exists. Want me to change its date instead?"),
    ])
    assert out["tool_calls"][0]["ok"] is False
    assert len(client.get("/tasks", headers=u).json()) == 1


def test_repeated_identical_failure_stops_early(client):
    u = signup(client, "stuck")
    bad = {"task_id": 999999, "status": "done"}
    out = chat(client, u, "mark it done", [
        reply(calls=[call(47, "update_task", bad)]),
        reply(calls=[call(48, "update_task", bad)]),
        reply("never reached"),
    ])
    assert out["error"] is True and "not found" in out["reply"]
    assert len(out["tool_calls"]) == 2


def test_rate_limit_fails_fast_with_wait_time(client):
    import groq
    import httpx
    resp = httpx.Response(429, headers={"retry-after": "7"}, request=httpx.Request("POST", "https://api.groq.com"))
    u = signup(client, "throttled")
    out = chat(client, u, "add", [groq.RateLimitError("rate limited", response=resp, body=None)])
    assert out["error"] is True and "7 seconds" in out["reply"]


def test_assigned_tasks_show_who_assigned_them(client):
    worker = signup(client, "intern")
    chat(client, worker, "add", [reply(calls=[call(50, "add_task", {"title": "My own task"})]), reply("ok")])
    boss = login(client, "boss", "bosspass123")
    chat(client, boss, "assign", [
        reply(calls=[call(51, "assign_task", {"username": "intern", "title": "Review deck", "due_date": "friday"})]),
        reply("Assigned."),
    ])
    tasks = {t["title"]: t["assigned_by"] for t in client.get("/tasks", headers=worker).json()}
    assert tasks == {"My own task": None, "Review deck": "boss"}

    seen.clear()
    out = chat(client, worker, "what do i have", [reply(calls=[call(52, "list_tasks", {})]), reply("You have 2 tasks.")])
    assert "'Review deck'" in seen[0][0] and "assigned by boss" in seen[0][0]
    listed = json.loads(seen[1][1]["content"])["tasks"]
    assert {t["title"]: t.get("assigned_by") for t in listed} == {"My own task": None, "Review deck": "boss"}


def test_time_inside_date_is_split_out(client):
    u = signup(client, "timer")
    out = chat(client, u, "add", [
        reply(calls=[call(60, "add_task", {"title": "Call mom", "due_date": "tomorrow at 5pm"})]), reply("ok"),
    ])
    assert out["tool_calls"][0]["ok"]
    task = client.get("/tasks", headers=u).json()[0]
    assert task["due_time"] == "17:00:00"


def test_time_without_date_picks_today_or_tomorrow(client):
    from app.agent.dates import today
    from datetime import timedelta
    u = signup(client, "timeonly")
    chat(client, u, "add", [reply(calls=[call(61, "add_task", {"title": "Stretch", "due_time": "11:59 pm"})]), reply("ok")])
    task = client.get("/tasks", headers=u).json()[0]
    assert task["due_date"] in (today().isoformat(), (today() + timedelta(days=1)).isoformat())
    assert task["due_time"] == "23:59:00"


def test_ambiguous_hour_is_rejected(client):
    u = signup(client, "vague")
    out = chat(client, u, "add", [
        reply(calls=[call(62, "add_task", {"title": "Gym", "due_date": "tomorrow", "due_time": "5"})]),
        reply("Do you mean 5 am or 5 pm?"),
    ])
    assert out["tool_calls"][0]["ok"] is False
    assert client.get("/tasks", headers=u).json() == []


def test_update_time_and_clear_it(client):
    u = signup(client, "retimer")
    chat(client, u, "add", [reply(calls=[call(63, "add_task", {"title": "Meeting", "due_date": "17 june"})]), reply("ok")])
    tid = client.get("/tasks", headers=u).json()[0]["id"]
    chat(client, u, "make it 9:30am", [reply(calls=[call(64, "update_task", {"task_id": tid, "due_time": "9:30am"})]), reply("ok")])
    assert client.get("/tasks", headers=u).json()[0]["due_time"] == "09:30:00"
    chat(client, u, "no time", [reply(calls=[call(65, "update_task", {"task_id": tid, "clear_due_time": True})]), reply("ok")])
    assert client.get("/tasks", headers=u).json()[0]["due_time"] is None


def test_startup_adds_missing_time_column():
    """An existing database from before times were added gets the column on startup."""
    import sqlalchemy as sa
    from app import main as main_module

    path = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sa.create_engine(f"sqlite:///{path}")
    with old.begin() as conn:
        conn.execute(sa.text("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title VARCHAR(200), due_date DATE)"))
    original = main_module.engine
    main_module.engine = old
    try:
        main_module._add_missing_columns()
        main_module._add_missing_columns()  # running twice is harmless
    finally:
        main_module.engine = original
    assert {"due_time", "updated_at"} <= {c["name"] for c in sa.inspect(old).get_columns("tasks")}


def test_acknowledgement_gets_read_only_tools(client):
    u = signup(client, "acker")
    chat(client, u, "add", [reply(calls=[call(70, "add_task", {"title": "Be polite"})]), reply("Added 'Be polite'.")])
    tid = client.get("/tasks", headers=u).json()[0]["id"]
    seen_calls.clear()
    out = chat(client, u, "okk cool", [
        reply(calls=[call(71, "update_task", {"task_id": tid, "status": "done"})]),  # model tries anyway
        reply("Okay!"),
    ])
    assert seen_calls[0][1] == ["list_tasks"]
    assert out["tool_calls"][0]["ok"] is False
    assert client.get("/tasks", headers=u).json()[0]["status"] == "pending"


def test_ok_after_a_question_is_a_yes(client):
    u = signup(client, "confirmer")
    chat(client, u, "add", [reply(calls=[call(72, "add_task", {"title": "Gym"})]),
                            reply("Added 'Gym'. Want me to mark it done too?")])
    tid = client.get("/tasks", headers=u).json()[0]["id"]
    seen_calls.clear()
    out = chat(client, u, "ok", [reply(calls=[call(73, "update_task", {"task_id": tid, "status": "done"})]),
                                 reply("Marked 'Gym' done.")])
    assert "update_task" in seen_calls[0][1] and out["error"] is False


def test_history_tells_the_model_what_actually_ran(client):
    u = signup(client, "historian")
    chat(client, u, "add", [reply(calls=[call(74, "add_task", {"title": "Be polite"})]), reply("ok")])
    tid = client.get("/tasks", headers=u).json()[0]["id"]
    chat(client, u, "mark be polite done", [
        reply(calls=[call(75, "update_task", {"task_id": tid, "status": "done"})]), reply("Marked 'Be polite' as done."),
    ])
    seen_calls.clear()
    chat(client, u, "why did you mark be polite done", [reply("You asked me to earlier.")])
    history = seen_calls[0][0]
    note = history[-2]["content"]
    assert history[-2]["role"] == "assistant" and f"marked done 'Be polite' (id {tid})" in note
    assert "'Be polite'" in history[0]["content"].split("Recently completed")[1]  # in the done list


def test_chat_history_api_keeps_tool_chips(client):
    u = signup(client, "chips")
    chat(client, u, "add", [reply(calls=[call(76, "add_task", {"title": "Laundry"})]), reply("Added 'Laundry'.")])
    msgs = client.get("/chat/history", headers=u).json()
    assert msgs[-1]["tool_calls"][0]["tool"] == "add_task"
    assert msgs[-1]["tool_calls"][0]["summary"].startswith("added 'Laundry'")
    assert msgs[0]["tool_calls"] == []


def test_admitting_an_earlier_action_is_not_a_false_claim(client):
    u = signup(client, "admitter")
    out = chat(client, u, "why did you do that", [
        reply("Sorry, I marked 'be polite' done earlier by mistake. Want me to undo it?"),
    ])
    assert out["error"] is False and "by mistake" in out["reply"]


def test_updated_at_is_set_on_change(client):
    from app.models import Task
    u = signup(client, "stamper")
    chat(client, u, "add", [reply(calls=[call(77, "add_task", {"title": "Stamp"})]), reply("ok")])
    tid = client.get("/tasks", headers=u).json()[0]["id"]
    with SessionLocal() as db:
        assert db.get(Task, tid).updated_at is not None


def test_manual_tick_and_delete_only_own_tasks(client):
    owner = signup(client, "ticker")
    other = signup(client, "nosy")
    boss = login(client, "boss", "bosspass123")
    chat(client, owner, "add", [reply(calls=[call(80, "add_task", {"title": "Tick me"})]), reply("ok")])
    tid = client.get("/tasks", headers=owner).json()[0]["id"]

    for headers in (other, boss):  # another user, and an admin, both get "not found"
        assert client.patch(f"/tasks/{tid}", json={"status": "done"}, headers=headers).status_code == 404
        assert client.delete(f"/tasks/{tid}", headers=headers).status_code == 404
    assert client.patch(f"/tasks/{tid}", json={"status": "done", "owner_id": 1}, headers=owner).status_code == 422
    assert client.patch(f"/tasks/{tid}", json={"status": "finished"}, headers=owner).status_code == 422
    assert client.patch(f"/tasks/{tid}", json={"status": "done"}).status_code == 401

    assert client.patch(f"/tasks/{tid}", json={"status": "done"}, headers=owner).status_code == 204
    assert client.get("/tasks", headers=owner).json()[0]["status"] == "done"
    assert client.patch(f"/tasks/{tid}", json={"status": "pending"}, headers=owner).status_code == 204
    assert client.delete(f"/tasks/{tid}", headers=owner).status_code == 204
    assert client.get("/tasks", headers=owner).json() == []
