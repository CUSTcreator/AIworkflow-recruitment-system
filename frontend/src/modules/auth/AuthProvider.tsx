import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";
import type { AuthUser, LoginResponse } from "@/modules/auth/contracts";
import { requestJson } from "@/shared/api/httpClient";
const TOKEN_KEY = "recruit-ai-access-token";

interface AuthContextValue {
  token: string | null;
  user: AuthUser | null;
  checking: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => window.localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(Boolean(token));

  const logout = useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setChecking(false);
  }, []);

  useEffect(() => {
    if (!token) {
      setChecking(false);
      return;
    }
    setChecking(true);
    requestJson<AuthUser>(token, "/auth/me")
      .then(setUser)
      .catch(logout)
      .finally(() => setChecking(false));
  }, [logout, token]);

  const login = useCallback(async (username: string, password: string) => {
    const payload = await requestJson<LoginResponse>(null, "/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    });
    window.localStorage.setItem(TOKEN_KEY, payload.accessToken);
    setToken(payload.accessToken);
    setUser(payload.user);
  }, []);

  const value = useMemo(() => ({ token, user, checking, login, logout }), [checking, login, logout, token, user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
