import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const EXAMPLES = [
  "Add a task to submit the monthly report by Friday",
  "What's due this week?",
  "Mark the report task as done and add a task to start the next one",
];

const TOOL_LABELS = {
  add_task: "added task",
  list_tasks: "looked up tasks",
  update_task: "updated task",
  delete_task: "deleted task",
  assign_task: "assigned task",
};

function ToolTrail({ calls }) {
  if (!calls?.length) return null;
  return (
    <ul className="tool-trail" aria-label="Actions taken">
      {calls.map((c, i) => (
        <li key={i} className={c.ok ? "" : "failed"} title={c.summary || JSON.stringify(c.arguments)}>
          {TOOL_LABELS[c.tool] || c.tool}
          {!c.ok && " (failed)"}
        </li>
      ))}
    </ul>
  );
}

export default function ChatPanel({ onTasksChanged }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState("");
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    api("/chat/history")
      .then((rows) => setMessages(rows.map((r) => ({ role: r.role, content: r.content, toolCalls: r.tool_calls }))))
      .catch((e) => setLoadError(e.message));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, sending]);

  const send = async (text) => {
    const message = text.trim();
    if (!message || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    setSending(true);
    try {
      const res = await api("/chat", { method: "POST", body: { message } });
      setMessages((m) => [...m, { role: "assistant", content: res.reply, toolCalls: res.tool_calls, error: res.error }]);
      if (res.tool_calls.length) onTasksChanged?.();
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: e.message, error: true }]);
    } finally {
      setSending(false);
      inputRef.current?.focus(); // keep typing without clicking back into the box
    }
  };

  const clear = async () => {
    try {
      await api("/chat/history", { method: "DELETE" });
      setMessages([]);
    } catch (e) {
      setLoadError(e.message);
    }
  };

  return (
    <section className="chat" aria-label="Chat with TaskPilot">
      <div className="chat-head">
        <h2>Assistant</h2>
        {messages.length > 0 && <button className="btn-quiet" onClick={clear}>Clear chat</button>}
      </div>

      <div className="chat-log" aria-live="polite">
        {loadError && <p className="error">{loadError}</p>}
        {messages.length === 0 && !loadError && (
          <div className="chat-empty">
            <p>Tell me what to add, change or finish. Try one of these:</p>
            {EXAMPLES.map((ex) => (
              <button key={ex} className="example" onClick={() => send(ex)}>{ex}</button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg msg-${m.role} ${m.error ? "msg-error" : ""}`}>
            <p>{m.content}</p>
            {m.role === "assistant" && <ToolTrail calls={m.toolCalls} />}
          </div>
        ))}
        {sending && <div className="msg msg-assistant msg-pending"><p>Working on it…</p></div>}
        <div ref={endRef} />
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <label htmlFor="chat-box" className="sr-only">Message</label>
        <input
          id="chat-box"
          ref={inputRef}
          autoFocus
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. Move the report to next Monday"
          maxLength={2000}
          autoComplete="off"
        />
        <button type="submit" className="btn" disabled={sending || !input.trim()}>Send</button>
      </form>
    </section>
  );
}
