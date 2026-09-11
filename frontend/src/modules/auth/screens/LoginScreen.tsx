import { LockKeyhole, UserRound } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/modules/auth/AuthProvider";

export function LoginScreen() {
  const { user, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  useEffect(() => setError(""), [username, password]);

  if (user) return <Navigate to="/" replace />;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <section className="w-full max-w-sm border border-line bg-white p-7 shadow-sm">
        <div className="mb-7">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-blue-600 text-white">
            <LockKeyhole size={21} aria-hidden="true" />
          </div>
          <h1 className="text-xl font-semibold text-ink">AI 招聘系统</h1>
          <p className="mt-2 text-sm text-muted">使用部门主管或 HR 账号登录</p>
        </div>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700">账号</span>
            <div className="flex items-center gap-2 rounded-md border border-line px-3 focus-within:border-blue-500">
              <UserRound size={17} className="text-muted" aria-hidden="true" />
              <input
                className="h-10 min-w-0 flex-1 border-0 bg-transparent text-sm outline-none"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </div>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700">密码</span>
            <input
              className="h-10 w-full rounded-md border border-line px-3 text-sm outline-none focus:border-blue-500"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? <p className="text-sm text-red-600" role="alert">{error}</p> : null}
          <button
            className="h-10 w-full rounded-md bg-blue-600 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            type="submit"
            disabled={submitting}
          >
            {submitting ? "正在登录" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
