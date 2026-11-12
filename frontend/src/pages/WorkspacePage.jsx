import ChatPanel from "../components/ChatPanel";
import TaskList from "../components/TaskList";
import { useTasks } from "../useTasks";

// Chat on the left, live task list on the right, so every change the agent makes is visible.
export default function WorkspacePage() {
  const tasks = useTasks();
  return (
    <main className="workspace">
      <ChatPanel onTasksChanged={tasks.refresh} />
      <TaskList {...tasks} compact />
    </main>
  );
}
