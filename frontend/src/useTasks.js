import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

// Loads the user's tasks and remembers which ones changed since the last load,
// so the list can highlight what the agent just did.
export function useTasks() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [changedIds, setChangedIds] = useState(new Set());
  const previous = useRef(null);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api("/tasks");
      if (previous.current) {
        const before = new Map(previous.current.map((t) => [t.id, JSON.stringify(t)]));
        const changed = new Set(next.filter((t) => before.get(t.id) !== JSON.stringify(t)).map((t) => t.id));
        setChangedIds(changed);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setChangedIds(new Set()), 2200);
      }
      previous.current = next;
      setTasks(next);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    return () => clearTimeout(timer.current);
  }, [refresh]);

  return { tasks, loading, error, changedIds, refresh };
}
