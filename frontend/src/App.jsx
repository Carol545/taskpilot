import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Nav from "./components/Nav";
import LoginPage from "./pages/LoginPage";
import WorkspacePage from "./pages/WorkspacePage";
import TasksPage from "./pages/TasksPage";
import AdminPage from "./pages/AdminPage";

function Protected({ children, adminOnly = false }) {
  const { user, checking } = useAuth();
  if (checking) return <p className="page-note">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (adminOnly && user.role !== "admin") return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  const { user } = useAuth();
  return (
    <>
      {user && <Nav />}
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
        {/* key={user?.id}: when the logged-in user changes, pages reload their data from scratch */}
        <Route path="/" element={<Protected><WorkspacePage key={user?.id} /></Protected>} />
        <Route path="/tasks" element={<Protected><TasksPage key={user?.id} /></Protected>} />
        <Route path="/admin" element={<Protected adminOnly><AdminPage key={user?.id} /></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
