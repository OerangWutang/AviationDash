import { useState } from "react";
import type { MfaEnrollment } from "../api/client";
import { useStore } from "../state/store";

export function MfaPage() {
  const { state, dismissMfa, startMfaEnrollment, enableMfa, verifyMfa } = useStore();
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mfaEnabled = state.mfa?.enabled ?? false;
  const title = mfaEnabled ? "Verify MFA" : "Set up MFA";
  const blocker = code.trim().length < 6 ? "Enter the 6-digit code." : null;

  const handleStart = async () => {
    setBusy(true);
    setError(null);
    try {
      const outcome = await startMfaEnrollment();
      if (outcome.ok) {
        setEnrollment(outcome.enrollment);
      } else {
        setError(outcome.error);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async () => {
    if (busy || blocker) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = mfaEnabled ? await verifyMfa(code) : await enableMfa(code);
      if (!outcome.ok) {
        setError(outcome.error);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="boot-screen">
      <p className="boot-title">Atlas Argus</p>
      <form
        className="panel login-panel"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
      >
        <h1 className="login-heading">{title}</h1>
        {!mfaEnabled && (
          <>
            <p className="muted login-note">
              Add this TOTP secret to your authenticator, then enter the current code.
            </p>
            {enrollment ? (
              <div className="mfa-secret-box">
                <span className="muted">Secret</span>
                <strong className="mono">{enrollment.secret}</strong>
                <span className="muted mono">{enrollment.otpauthUri}</span>
              </div>
            ) : (
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => void handleStart()}
              >
                Start setup
              </button>
            )}
          </>
        )}
        {mfaEnabled && (
          <p className="muted login-note">
            Enter the current code from your authenticator to unlock high-risk actions.
          </p>
        )}
        {(mfaEnabled || enrollment) && (
          <label className="field">
            <span>MFA code</span>
            <input
              value={code}
              inputMode="numeric"
              autoComplete="one-time-code"
              onChange={(event) => {
                setCode(event.target.value);
                setError(null);
              }}
            />
          </label>
        )}
        <div className="save-row">
          {(mfaEnabled || enrollment) && (
            <button
              type="submit"
              className="btn-primary"
              disabled={busy || blocker !== null}
            >
              {busy ? "Verifying…" : mfaEnabled ? "Verify" : "Enable MFA"}
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={dismissMfa}>
            Cancel
          </button>
          {(mfaEnabled || enrollment) && blocker && (
            <span className="save-blocker">{blocker}</span>
          )}
        </div>
        {error && <p className="form-error">{error}</p>}
      </form>
    </div>
  );
}
