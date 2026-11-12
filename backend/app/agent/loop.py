"""
The agent loop.

  1. Build messages: system prompt + recent chat history + the new user message.
  2. Send them to the LLM along with the tools this user is allowed to use.
     The system prompt already lists the user's pending tasks with ids, so most
     requests need no list_tasks round first.
  3. If the LLM asks for tool calls, run each one (our code, scoped to
     current_user), append the results, and go back to step 2.
  4. If the LLM replies with plain text, check its claims against what
     actually ran this turn. A reply that says "added"/"deleted"/etc. with
     no matching successful tool call is sent back for correction once,
     and replaced with an honest message if it still doesn't match.
  5. Stop early if the same tool call fails twice, and after MAX_ITERATIONS
     rounds no matter what.

Two more guards against the model acting on its own or misremembering:
  * A pure acknowledgement ("ok", "thanks", "lol") gets only read-only tools,
    unless the assistant just asked a question (then "ok" is a yes).
  * Each saved reply stores what actually ran, and the history shown to the
    model includes it, so it can't deny or invent past actions.

The LLM only proposes actions. execute_tool() decides whether they're allowed,
and verify_claims() decides whether the reply is allowed to say they happened.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import groq
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.agent.prompts import build_system_prompt
from app.agent.tools import execute_tool, tool_schemas_for
from app.config import settings
from app.models import ChatMessage, Task, User

log = logging.getLogger(__name__)

MAX_ITERATIONS = 6
HISTORY_LIMIT = 10      # past messages sent to the LLM; keeps requests small and under rate limits
MAX_FORMAT_RETRIES = 2  # malformed tool calls fed back to the model
MAX_CLAIM_CORRECTIONS = 1

_client: groq.Groq | None = None


def get_client() -> groq.Groq:
    global _client
    if _client is None:
        # No automatic retries: the SDK would silently wait out rate limits (we saw 20s stalls).
        # _call_llm retries network blips itself; rate limits are reported to the user straight away.
        _client = groq.Groq(api_key=settings.groq_api_key, timeout=30.0, max_retries=0)
    return _client


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    error: bool = False


# ---------------------------------------------------------------------------
# Claim verification: does the reply say something happened that didn't?
# ---------------------------------------------------------------------------

# Verb in the reply -> tools that would make that claim true.
_CLAIM_VERBS: dict[str, set[str]] = {
    "added": {"add_task", "assign_task"},
    "created": {"add_task", "assign_task"},
    "updated": {"update_task"},
    "marked": {"update_task"},
    "moved": {"update_task"},
    "changed": {"update_task"},
    "renamed": {"update_task"},
    "rescheduled": {"update_task"},
    "completed": {"update_task"},
    "deleted": {"delete_task"},
    "removed": {"delete_task"},
    "assigned": {"assign_task"},
}
_VERBS = "|".join(_CLAIM_VERBS)
# Only affirmative, first-person-ish claims: "Added ...", "I've deleted ...", "It has been marked ...".
# Not "was added on Monday" (describing existing tasks) or "I couldn't delete it".
_CLAIM_PATTERN = re.compile(
    rf"^\s*(?:done[.!,]?\s*)?(?:(?:i\s*(?:'ve|have)?|we\s*(?:'ve|have)?)\s+)?(?:just\s+)?({_VERBS})\b"
    rf"|\b(?:has|have)\s+been\s+({_VERBS})\b"
    rf"|\band\s+(?:also\s+)?({_VERBS})\b"
    rf"|\b(?:it'?s|it\s+is|they'?re|they\s+are)\s+(?:now\s+)?({_VERBS})\b",
    re.IGNORECASE,
)
# Sentences about earlier turns ("I marked it done earlier by mistake") aren't claims about this turn.
_PAST_REFERENCE = re.compile(r"\b(earlier|previous(ly)?|before|last (time|turn|message)|by mistake|mistakenly|already|yesterday)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|no|never|nothing|couldn'?t|can'?t|didn'?t|unable|failed|wasn'?t|haven'?t)\b", re.IGNORECASE)


def unverified_claims(reply: str, trace: list[dict]) -> list[str]:
    """Return claimed verbs that have no matching successful tool call this turn."""
    succeeded = {t["tool"] for t in trace if t["ok"]}
    missing: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", reply):
        if _NEGATION.search(sentence) or _PAST_REFERENCE.search(sentence):
            continue
        for match in _CLAIM_PATTERN.finditer(sentence):
            verb = next(g for g in match.groups() if g).lower()
            if not (_CLAIM_VERBS[verb] & succeeded) and verb not in missing:
                missing.append(verb)
    return missing


def summarize_action(name: str, args: dict | str, output: dict) -> str:
    """Plain-English record of one tool call, e.g. "marked 'be polite' done (id 12)"."""
    if "error" in output:
        return f"{name} failed: {output['error']}"
    if name == "add_task":
        t = output["added"]
        due = f", due {t['due_label']}" if t.get("due_label") else ""
        return f"added '{t['title']}' (id {t['id']}{due})"
    if name == "update_task":
        t = output["updated"]
        a = args if isinstance(args, dict) else {}
        changes = []
        if a.get("status"):
            changes.append("marked done" if a["status"] == "done" else "marked not done")
        if a.get("title"):
            changes.append("renamed")
        if a.get("due_date") or a.get("due_time") or a.get("clear_due_time"):
            changes.append(f"due {t['due_label']}" if t.get("due_label") else "due date changed")
        return f"{' and '.join(changes) or 'updated'} '{t['title']}' (id {t['id']})"
    if name == "delete_task":
        return f"deleted '{output['deleted']['title']}' (id {output['deleted']['id']})"
    if name == "assign_task":
        return f"assigned '{output['task']['title']}' to {output['assigned_to']}"
    if name == "list_tasks":
        return f"looked up tasks ({output.get('count', 0)} found)"
    return f"ran {name}"


def _honest_fallback(trace: list[dict]) -> str:
    done = [t["tool"] for t in trace if t["ok"] and t["tool"] != "list_tasks"]
    if done:
        return (f"I only completed part of that: {', '.join(done)}. "
                "Check your task list and tell me what's still missing.")
    return "I didn't make any changes. Could you say that again, a bit more specifically?"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _action_note(actions_json: str | None) -> str:
    """'[Actions actually taken this turn: marked 'be polite' done (id 12)]' for the model's history."""
    if actions_json is None:
        return ""  # saved before actions were recorded
    actions = [a for a in json.loads(actions_json) if a.get("tool") != "list_tasks"]
    if not actions:
        return "\n[System note: no tasks were changed in this turn.]"
    parts = [a.get("summary") or f"{a['tool']} {'succeeded' if a['ok'] else 'failed'}" for a in actions]
    return "\n[System note, actions actually taken this turn: " + "; ".join(parts) + "]"


def _load_history(user: User, db: Session) -> list[ChatMessage]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id)  # scoped like everything else
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return list(reversed(rows))


def _history_messages(rows: list[ChatMessage]) -> list[dict]:
    return [
        {"role": m.role, "content": m.content + (_action_note(m.actions) if m.role == "assistant" else "")}
        for m in rows
    ]


# ---------------------------------------------------------------------------
# Acknowledgement guard
# ---------------------------------------------------------------------------

_ACK_WORDS = {
    "ok", "okay", "okk", "okkk", "okie", "k", "kk", "cool", "nice", "great", "thanks", "thank", "you",
    "thx", "ty", "tysm", "got", "it", "alright", "fine", "noted", "perfect", "awesome", "good", "lol",
    "lmao", "haha", "hahaha", "wow", "yay", "sweet", "np", "kthx", "makes", "sense", "ah", "oh",
}


def is_acknowledgement(message: str) -> bool:
    words = re.findall(r"[a-z]+", message.lower())
    return bool(words) and len(words) <= 6 and all(w in _ACK_WORDS for w in words)


READ_ONLY_TOOLS = {"list_tasks"}


def _parse_args(raw: str | None) -> dict | str:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else str(raw)
    except json.JSONDecodeError:
        return raw or ""


def _tool_error_message(e: groq.BadRequestError) -> str | None:
    """If Groq rejected a malformed tool call, return its explanation."""
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    if isinstance(err, dict) and err.get("code") == "tool_use_failed":
        return str(err.get("message", "tool call did not match the schema"))[:300]
    return None


def _summarize_actions(trace: list[dict]) -> str:
    done = [t for t in trace if t["ok"] and t["tool"] != "list_tasks"]
    if not done:
        return "I couldn't finish that request. Try breaking it into smaller steps."
    names = ", ".join(t["tool"] for t in done)
    return (
        f"I ran out of steps before finishing. Actions that did go through: {names}. "
        "Check your task list, then ask me again for anything that's missing."
    )


def _call_llm(messages: list[dict], tools: list[dict]):
    kwargs = dict(
        model=settings.groq_model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0,  # we want consistent tool use, not creativity
    )
    if settings.groq_reasoning_effort:
        kwargs["reasoning_effort"] = settings.groq_reasoning_effort
    try:
        return get_client().chat.completions.create(**kwargs)
    except (groq.APIConnectionError, groq.InternalServerError):
        time.sleep(0.5)  # one quick retry for network blips and Groq 5xx
        return get_client().chat.completions.create(**kwargs)


def _retry_after(e: groq.RateLimitError) -> int | None:
    try:
        return max(1, round(float(e.response.headers.get("retry-after"))))
    except (AttributeError, TypeError, ValueError):
        return None


def _pending_tasks(user: User, db: Session) -> list[tuple[Task, str | None]]:
    """The user's pending tasks, each with the admin's username if an admin assigned it."""
    creator = aliased(User)
    rows = db.execute(
        select(Task, creator.username)
        .join(creator, Task.created_by_id == creator.id)
        .where(Task.owner_id == user.id, Task.status == "pending")
        .order_by(Task.due_date.is_(None), Task.due_date, Task.due_time.is_(None), Task.due_time, Task.id)
    ).all()
    return [(t, name if t.created_by_id != t.owner_id else None) for t, name in rows]


def _recently_done(user: User, db: Session, limit: int = 10) -> list[Task]:
    return list(db.scalars(
        select(Task)
        .where(Task.owner_id == user.id, Task.status == "done")
        .order_by(Task.updated_at.is_(None), Task.updated_at.desc(), Task.id.desc())
        .limit(limit)
    ).all())


def _call_signature(name: str, raw_args: str | None) -> str:
    try:
        return name + json.dumps(json.loads(raw_args or "{}"), sort_keys=True)
    except json.JSONDecodeError:
        return name + (raw_args or "")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run_agent(user: User, db: Session, user_message: str) -> AgentResult:
    history = _load_history(user, db)
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(user, _pending_tasks(user, db), _recently_done(user, db))},
        *_history_messages(history),
        {"role": "user", "content": user_message},
    ]
    tools = tool_schemas_for(user)
    offered: set[str] | None = None
    last_reply = next((m.content for m in reversed(history) if m.role == "assistant"), "")
    if is_acknowledgement(user_message) and not last_reply.rstrip().endswith("?"):
        # "ok cool" after a statement is not a request. Don't even offer the write tools.
        tools = [t for t in tools if t["function"]["name"] in READ_ONLY_TOOLS]
        offered = READ_ONLY_TOOLS
    trace: list[dict] = []
    result: AgentResult | None = None
    format_retries = MAX_FORMAT_RETRIES
    claim_corrections = MAX_CLAIM_CORRECTIONS
    failed_calls: set[str] = set()

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            response = _call_llm(messages, tools)
        except groq.RateLimitError as e:
            wait = _retry_after(e)
            text = "The AI service is rate-limited right now. " + (
                f"Try again in about {wait} seconds." if wait else "Try again in a few seconds.")
            done = [t["tool"] for t in trace if t["ok"] and t["tool"] != "list_tasks"]
            if done:
                text += f" Before that, these went through: {', '.join(done)}."
            result = AgentResult(text, trace, True)
            break
        except (groq.APITimeoutError, groq.APIConnectionError):
            result = AgentResult("The AI service took too long to respond. Try again.", trace, True)
            break
        except groq.BadRequestError as e:
            problem = _tool_error_message(e)
            log.warning("LLM bad request: %s", e)
            if problem and format_retries > 0:
                # Retrying the same messages at temperature 0 gives the same broken output,
                # so tell the model what was wrong.
                format_retries -= 1
                messages.append({
                    "role": "user",
                    "content": f"[System check] Your last tool call was rejected: {problem}. "
                               "Call the tool again using the tool-calling interface with valid JSON "
                               "arguments, including every required field. Don't write tool calls as text.",
                })
                continue
            result = AgentResult("I had trouble with that request. Could you rephrase it?", trace, True)
            break
        except groq.APIError:
            log.exception("LLM error")
            result = AgentResult("The AI service returned an error. Try again in a moment.", trace, True)
            break

        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = (msg.content or "").strip() or "Done."
            missing = unverified_claims(reply, trace)
            if not missing:
                result = AgentResult(reply, trace)
                break
            log.warning("Unverified claim %s for user %s: %r", missing, user.id, reply)
            if claim_corrections > 0:
                claim_corrections -= 1
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": f"[System check] Your reply says a task was {', '.join(missing)}, but no "
                               "matching tool call succeeded in this turn. If that change is still needed, "
                               "call the tool now. Otherwise rewrite your reply so it only describes what "
                               "actually happened.",
                })
                continue
            result = AgentResult(_honest_fallback(trace), trace, error=True)
            break

        # Keep the model's tool-call message in the conversation so it can see its own steps.
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        repeated_failure: dict | None = None
        for tc in msg.tool_calls:
            output = execute_tool(tc.function.name, tc.function.arguments, user, db, offered)
            ok = "error" not in output
            args = _parse_args(tc.function.arguments)
            trace.append({"tool": tc.function.name, "arguments": args, "ok": ok,
                          "summary": summarize_action(tc.function.name, args, output)})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(output)})
            if not ok:
                sig = _call_signature(tc.function.name, tc.function.arguments)
                if sig in failed_calls:
                    repeated_failure = output
                failed_calls.add(sig)

        if repeated_failure:
            # The model is stuck retrying the same rejected call. Stop and say why.
            detail = repeated_failure["error"].split(". Ask")[0].split(". If")[0]
            if repeated_failure.get("problems"):
                detail += " " + "; ".join(repeated_failure["problems"])
            result = AgentResult(f"I couldn't do that: {detail}", trace, True)
            break

    if result is None:
        result = AgentResult(_summarize_actions(trace), trace, error=True)

    # Save the turn unless nothing happened at all (e.g. the LLM was down).
    # Replies that failed verification were already replaced, so no false claim is stored.
    if not (result.error and not trace):
        db.add_all([
            ChatMessage(user_id=user.id, role="user", content=user_message),
            ChatMessage(user_id=user.id, role="assistant", content=result.reply,
                        actions=json.dumps(result.tool_calls, default=str)),
        ])
        db.commit()

    return result
