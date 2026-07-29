import { useState } from "react";
import { useStore } from "../state/store";

export function LoginPage() {
  const { login } = useStore();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);

  const handleSubmit = async () => {
    if (signingIn || !username || !password) return;
    setSigningIn(true);
    try {
      const outcome = await login(username, password);
      if (!outcome.ok) {
        setError(outcome.error);
      }
    } finally {
      setSigningIn(false);
    }
  };

  return (
    <div className="boot-screen">
      <p className="boot-title">Atlas Argus</p>
      <form
        className="panel login-panel"
        onSubmit={(e) => {
          e.preventDefault();
          void handleSubmit();
        }}
      >
        <h1 className="login-heading">Sign in</h1>
        <p className="muted login-note">
          Evidence review is identity-bound: every decision, extraction, and export is
          recorded against your account.
        </p>
        <label className="field">
          <span>Username</span>
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => {
              setUsername(e.target.value);
              setError(null);
            }}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setError(null);
            }}
          />
        </label>
        <div className="save-row">
          <button
            type="submit"
            className="btn-primary"
            disabled={signingIn || !username || !password}
          >
            {signingIn ? "Signing in…" : "Sign in"}
          </button>
        </div>
        {error && <p className="form-error">{error}</p>}
        {(import.meta.env.DEV || Boolean(import.meta.env.VITE_DEMO_LOGINS)) && (
          <p className="muted login-hint">
            Sample-case logins: <span className="mono">mokafor</span> (Senior Aviation
            Counsel), <span className="mono">dreeves</span> (Reconstruction Expert),{" "}
            <span className="mono">pnatarajan</span> (Claims Reviewer),{" "}
            <span className="mono">tlindqvist</span> (Safety Investigator) — password{" "}
            <span className="mono">argus-demo</span>.
          </p>
        )}
      </form>
    </div>
  );
}
