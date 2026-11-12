// "2026-11-14" (+ optional "17:00:00") -> "Sat 14 Nov, 5:00 pm", plus whether it's overdue/today.
function timeLabel(time) {
  const [h, m] = time.split(":").map(Number);
  const hour = h % 12 || 12;
  return `${hour}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
}

export function describeDue(iso, status, time = null) {
  if (!iso) return { label: "No date", tone: "none" };
  const [y, mo, d] = iso.split("-").map(Number);
  const due = new Date(y, mo - 1, d);
  const now = new Date();
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);

  const weekday = due.toLocaleDateString("en-GB", { weekday: "short" });
  const month = due.toLocaleDateString("en-GB", { month: "short" });
  const year = due.getFullYear() !== today.getFullYear() ? ` ${due.getFullYear()}` : ""; // "Thu 17 Jun 2027"
  const isToday = due.getTime() === today.getTime();
  let label = isToday ? "Today" : `${weekday} ${due.getDate()} ${month}${year}`;
  if (time) label += `, ${timeLabel(time)}`;

  if (status === "done") return { label, tone: "none" };

  // A timed task is overdue from that minute; a date-only task from the next day.
  let overdue = due < today;
  if (time && isToday) {
    const [h, m] = time.split(":").map(Number);
    const dueAt = new Date(due);
    dueAt.setHours(h, m, 0, 0);
    overdue = dueAt < now;
  }
  if (overdue) return { label: `${label}, overdue`, tone: "late" };
  if (isToday) return { label, tone: "soon" };
  return { label, tone: "none" };
}
