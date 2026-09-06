import { useEffect, useRef, useState } from "react";
import type { MfaEnrollment } from "../api/client";
import { useStore } from "../state/store";

export function MfaPage() {
  const { state, dismissMfa, startMfaEnrollment, enableMfa, verifyMfa } = useStore();
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const mfaEnabled = state.mfa?.enabled ?? false;
  const title = mfaEnabled ? "Verify MFA" : "Set up MFA";
  const blocker = code.trim().length < 6 ? "Enter the 6-digit code." : null;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    codeRef.current?.focus();
    return () => {
      if (dialog.open && typeof dialog.close === "function") dialog.close();
    };
  }, []);

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
    <dialog
      ref={dialogRef}
      className="mfa-dialog"
      aria-labelledby="mfa-title"
      aria-describedby="mfa-description"
      onCancel={(event) => {
        event.preventDefault();
        dismissMfa();
      }}
    >
      <form
        className="panel login-panel mfa-panel"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSubmit();
        }}
      >
        <p className="boot-title">Atlas Argus</p>
        <h1 id="mfa-title" className="login-heading">{title}</h1>
        {!mfaEnabled && (
          <>
            <p id="mfa-description" className="muted login-note">
              Add this TOTP secret to your authenticator, then enter the current code.
              Your current work remains open behind this verification step.
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
          <p id="mfa-description" className="muted login-note">
            Enter the current code from your authenticator to unlock high-risk actions.
            Your current work is preserved.
          </p>
        )}
        {(mfaEnabled || enrollment) && (
          <label className="field">
            <span>MFA code</span>
            <input
              ref={codeRef}
              value={code}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              aria-describedby={blocker ? "mfa-blocker" : undefined}
              onChange={(event) => {
                setCode(event.target.value.replace(/\D/g, "").slice(0, 6));
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
            <span id="mfa-blocker" className="save-blocker" aria-live="polite">
              {blocker}
            </span>
          )}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
    </dialog>
  );
}
