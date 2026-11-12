import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";

export default function Nav() {
  const { user, logout } = useAuth();
  return (
    <header className="nav">
      <span className="brand">TaskPilot</span>
      <nav className="nav-links">
        <NavLink to="/" end>Chat</NavLink>
        <NavLink to="/tasks">My tasks</NavLink>
        {user.role === "admin" && <NavLink to="/admin">Admin</NavLink>}
      </nav>
      <div className="nav-user">
        <span>
          {user.username}
          {user.role === "admin" && <span className="role-badge">admin</span>}
        </span>
        <button className="btn-quiet" onClick={logout}>Log out</button>
      </div>
    </header>
  );
}
