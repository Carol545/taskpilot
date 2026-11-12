import { useEffect, useState } from "react";
import { api } from "../api";
import { describeDue } from "../dates";

export default function AdminPage() {
  const [users, setUsers] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api("/admin/users"), api("/admin/tasks")])
      .then(([u, t]) => { setUsers(u); setTasks(t); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="page admin">
      <h1>Admin</h1>
      <p className="muted">Every user and every task. To give someone a task, ask the assistant, e.g. “Assign ravi a task to review the deck by Monday”.</p>
      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && (
        <>
          <h2>Users</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Username</th><th>Role</th><th>To do</th><th>Total tasks</th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{u.role}</td>
                    <td>{u.open_task_count}</td>
                    <td>{u.task_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>All tasks</h2>
          {tasks.length === 0 ? <p className="muted">No tasks yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Task</th><th>Owner</th><th>Due</th><th>Status</th><th>Created by</th></tr></thead>
                <tbody>
                  {tasks.map((t) => (
                    <tr key={t.id}>
                      <td>{t.title}</td>
                      <td>{t.owner}</td>
                      <td>{describeDue(t.due_date, t.status, t.due_time).label}</td>
                      <td>{t.status === "done" ? "Done" : "To do"}</td>
                      <td>{t.created_by}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
