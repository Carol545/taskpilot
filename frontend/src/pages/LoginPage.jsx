import { useState } from "react";
import { useAuth } from "../auth";

export default function LoginPage() {
  const { authenticate } = useAuth();
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await authenticate(mode, username, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const isLogin = mode === "login";
  return (
    <main className="login">
      <div className="login-intro">
        <h1 className="brand-big">TaskPilot</h1>
        <p>Your to-do list, run by chat. Say what needs doing and the assistant adds, updates and finishes tasks for you.</p>
      </div>

      <form className="login-card" onSubmit={submit}>
        <h2>{isLogin ? "Log in" : "Create an account"}</h2>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required minLength={3} />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isLogin ? "current-password" : "new-password"}
            required
            minLength={8}
          />
        </label>
        {!isLogin && <p className="hint">At least 8 characters. Usernames can use letters, numbers, dots, dashes and underscores.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "One moment…" : isLogin ? "Log in" : "Sign up"}
        </button>
        <button type="button" className="btn-link" onClick={() => { setMode(isLogin ? "signup" : "login"); setError(""); }}>
          {isLogin ? "New here? Create an account" : "Have an account? Log in"}
        </button>
      </form>
    </main>
  );
}
