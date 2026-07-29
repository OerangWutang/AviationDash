import { useState } from "react";
import { useStore } from "../state/store";

const MIN_PASSWORD_LENGTH = 10;

export function ChangePasswordPage({ forced }: { forced: boolean }) {
  const { changePassword, dismissPasswordChange, logout } = useStore();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  let blocker: string | null = null;
  if (!currentPassword) {
    blocker = "Enter your current password.";
  } else if (newPassword.length < MIN_PASSWORD_LENGTH) {
    blocker = `New password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  } else if (newPassword !== confirmPassword) {
    blocker = "New password and confirmation do not match.";
  }

  const handleSubmit = async () => {
    if (blocker || saving) return;
    setSaving(true);
    try {
      const outcome = await changePassword(currentPassword, newPassword);
      if (!outcome.ok) {
        setError(outcome.error);
      }
    } finally {
      setSaving(false);
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
        <h1 className="login-heading">Change password</h1>
        <p className="muted login-note">
          {forced
            ? "Your account uses the shared initial password — set a personal one before entering the case. Every other session for this account will be signed out."
            : "Rotating your password signs out every other session for this account."}
        </p>
        <label className="field">
          <span>Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => {
              setCurrentPassword(e.target.value);
              setError(null);
            }}
          />
        </label>
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => {
              setNewPassword(e.target.value);
              setError(null);
            }}
          />
        </label>
        <label className="field">
          <span>Confirm new password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
        </label>
        <div className="save-row">
          <button type="submit" className="btn-primary" disabled={blocker !== null || saving}>
            {saving ? "Saving…" : "Change password"}
          </button>
          {forced ? (
            <button type="button" className="btn-secondary" onClick={() => void logout()}>
              Sign out
            </button>
          ) : (
            <button type="button" className="btn-secondary" onClick={dismissPasswordChange}>
              Cancel
            </button>
          )}
          {blocker && <span className="save-blocker">{blocker}</span>}
        </div>
        {error && <p className="form-error">{error}</p>}
      </form>
    </div>
  );
}
