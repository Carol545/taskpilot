import { useState } from "react";
import { api } from "../api";
import { describeDue } from "../dates";

const FILTERS = [
  ["all", "All"],
  ["pending", "To do"],
  ["done", "Done"],
];

export default function TaskList({ tasks, loading, error, changedIds = new Set(), compact = false, refresh }) {
  const [filter, setFilter] = useState("all");
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState("");

  // Same permissions as the agent: the backend only lets you change your own tasks.
  const act = async (task, request) => {
    setBusyId(task.id);
    setActionError("");
    try {
      await request();
      await refresh?.();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusyId(null);
    }
  };
  const toggle = (t) =>
    act(t, () => api(`/tasks/${t.id}`, { method: "PATCH", body: { status: t.status === "done" ? "pending" : "done" } }));
  const remove = (t) => {
    if (window.confirm(`Delete "${t.title}"?`)) act(t, () => api(`/tasks/${t.id}`, { method: "DELETE" }));
  };
  const shown = filter === "all" ? tasks : tasks.filter((t) => t.status === filter);
  const openCount = tasks.filter((t) => t.status === "pending").length;

  return (
    <section className={`tasks ${compact ? "tasks-compact" : ""}`} aria-label="Your tasks">
      <div className="tasks-head">
        <h2>Tasks <span className="count">{openCount} to do</span></h2>
        <div className="filters" role="tablist">
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={filter === key}
              className={filter === key ? "active" : ""}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="muted">Loading tasks…</p>}
      {error && <p className="error">{error}</p>}
      {actionError && <p className="error" role="alert">{actionError}</p>}
      {!loading && !error && shown.length === 0 && (
        <p className="muted empty">
          {tasks.length === 0 ? "No tasks yet. Ask the assistant to add one." : "Nothing in this view."}
        </p>
      )}

      <ul className="task-list">
        {shown.map((t) => {
          const due = describeDue(t.due_date, t.status, t.due_time);
          return (
            <li key={t.id} className={`task ${t.status} ${changedIds.has(t.id) ? "flash" : ""}`}>
              <button
                className="check"
                onClick={() => toggle(t)}
                disabled={busyId === t.id}
                aria-label={t.status === "done" ? `Mark "${t.title}" as not done` : `Mark "${t.title}" as done`}
                title={t.status === "done" ? "Mark as not done" : "Mark as done"}
              >
                {t.status === "done" ? "✓" : ""}
              </button>
              <span className="task-title">
                {t.title}
                {t.assigned_by && <span className="assigned">from {t.assigned_by}</span>}
              </span>
              <span className={`due due-${due.tone}`}>{due.label}</span>
              <button
                className="delete"
                onClick={() => remove(t)}
                disabled={busyId === t.id}
                aria-label={`Delete "${t.title}"`}
                title="Delete"
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
