import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, tokenStore, TOKEN_KEY } from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(Boolean(tokenStore.get()));

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  // On load, turn a saved token back into a user. The backend decides the role.
  useEffect(() => {
    if (!tokenStore.get()) return;
    api("/auth/me")
      .then(setUser)
      .catch(logout)
      .finally(() => setChecking(false));
  }, [logout]);

  useEffect(() => {
    window.addEventListener("taskpilot:logout", logout);
    return () => window.removeEventListener("taskpilot:logout", logout);
  }, [logout]);

  // All tabs share one saved token. If another tab logs in or out, follow it here,
  // so this tab never shows one user while sending requests as another.
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key !== TOKEN_KEY) return;
      if (!e.newValue) {
        setUser(null);
        return;
      }
      api("/auth/me").then(setUser).catch(logout);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [logout]);

  const authenticate = async (mode, username, password) => {
    const data = await api(`/auth/${mode}`, { method: "POST", body: { username, password } });
    tokenStore.set(data.access_token);
    setUser(data.user);
  };

  return (
    <AuthContext.Provider value={{ user, checking, authenticate, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
