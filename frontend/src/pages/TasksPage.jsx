import TaskList from "../components/TaskList";
import { useTasks } from "../useTasks";

export default function TasksPage() {
  const tasks = useTasks();
  return (
    <main className="page">
      <TaskList {...tasks} />
    </main>
  );
}
