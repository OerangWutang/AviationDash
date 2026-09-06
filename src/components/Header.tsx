import { effectiveMatterRole, useStore } from "../state/store";

export function Header() {
  const {
    state,
    setReviewer,
    switchCase,
    logout,
    requestPasswordChange,
    requestMfa,
  } = useStore();
  const canSwitchMatter = state.dataMode === "server" && state.availableCases.length > 1;
  const matterRole = effectiveMatterRole(state);
  const mfaLabel =
    state.mfa?.enabled === true
      ? state.mfa.verified
        ? "MFA verified"
        : "Verify MFA"
      : "Set up MFA";

  return (
    <header className="app-header">
      <div className="brand">
        <svg className="brand-mark" viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
          <circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="2.5" />
          <circle cx="16" cy="16" r="4.5" fill="currentColor" />
        </svg>
        <span className="brand-name">Atlas Argus</span>
        <span className="brand-sub">Evidence Review</span>
      </div>
      <div className="header-case">
        {canSwitchMatter ? (
          <label className="matter-picker">
            <span className="visually-hidden">Matter</span>
            <select
              value={state.caseFile.id}
              onChange={(event) => void switchCase(event.target.value)}
              aria-label="Matter"
            >
              {state.availableCases.map(({ caseFile }) => (
                <option key={caseFile.id} value={caseFile.id}>
                  {caseFile.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="case-chip">{state.caseFile.name || "No matter selected"}</span>
        )}
        <span className="mono muted">{state.caseFile.docketRef}</span>
        <span
          className={`badge ${state.dataMode === "server" ? "tone-info" : "tone-neutral"}`}
          title={
            state.dataMode === "server"
              ? "Server mode: the case record lives in the Atlas Argus backend."
              : "Local mode: the case record lives in this browser only."
          }
        >
          {state.dataMode === "server" ? "server" : "local session"}
        </span>
      </div>
      {state.dataMode === "server" && state.sessionReviewer ? (
        <div className="session-identity">
          <span>
            <strong>{state.sessionReviewer.name}</strong>{" "}
            <span className="muted">
              {matterRole && matterRole !== state.sessionReviewer.role ? (
                <>
                  · Account: {state.sessionReviewer.role} · Matter: {matterRole}
                </>
              ) : (
                <>· {state.sessionReviewer.role}</>
              )}
            </span>
          </span>
          <button
            type="button"
            className="btn-secondary sign-out"
            onClick={requestPasswordChange}
          >
            Change password
          </button>
          <button type="button" className="btn-secondary sign-out" onClick={requestMfa}>
            {mfaLabel}
          </button>
          <button
            type="button"
            className="btn-secondary sign-out"
            onClick={() => void logout()}
          >
            Sign out
          </button>
        </div>
      ) : (
        <label className="reviewer-picker">
          <span className="muted">Acting as</span>
          <select
            value={state.activeReviewerId}
            onChange={(e) => setReviewer(e.target.value)}
            aria-label="Active reviewer"
          >
            {state.reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} — {r.role}
              </option>
            ))}
          </select>
        </label>
      )}
    </header>
  );
}
