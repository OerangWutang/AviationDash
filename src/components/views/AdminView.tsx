import { useEffect, useRef, useState } from "react";
import type {
  AccountAuditEvent,
  AdminCaseMember,
  AdminReviewer,
} from "../../api/client";
import type { ReviewerRole } from "../../domain/types";
import { useStore } from "../../state/store";

const ROLES: ReviewerRole[] = [
  "Senior Aviation Counsel",
  "Accident Reconstruction Expert",
  "Claims Reviewer",
  "Safety Investigator",
];

function upsertReviewer(
  reviewers: AdminReviewer[],
  reviewer: AdminReviewer,
): AdminReviewer[] {
  const exists = reviewers.some((r) => r.id === reviewer.id);
  const next = exists
    ? reviewers.map((r) => (r.id === reviewer.id ? reviewer : r))
    : [...reviewers, reviewer];
  return next.sort((a, b) => a.name.localeCompare(b.name));
}

function upsertMember(members: AdminCaseMember[], member: AdminCaseMember) {
  const exists = members.some((m) => m.reviewerId === member.reviewerId);
  const next = exists
    ? members.map((m) => (m.reviewerId === member.reviewerId ? member : m))
    : [...members, member];
  return next.sort((a, b) => a.reviewerName.localeCompare(b.reviewerName));
}

export function AdminView() {
  const {
    state,
    fetchAdminReviewers,
    fetchAdminAccountAudit,
    createAdminReviewer,
    setAdminReviewerActive,
    resetAdminReviewerPassword,
    resetAdminReviewerMfa,
    fetchCaseMembers,
    addCaseMember,
    setCaseMemberActive,
  } = useStore();
  const [reviewers, setReviewers] = useState<AdminReviewer[]>([]);
  const [caseMembers, setCaseMembers] = useState<AdminCaseMember[]>([]);
  const [accountAudit, setAccountAudit] = useState<AccountAuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [membersLoading, setMembersLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [memberBusyId, setMemberBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memberError, setMemberError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [memberNotice, setMemberNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<ReviewerRole>("Claims Reviewer");
  const [initialPassword, setInitialPassword] = useState("");
  const [memberReviewerId, setMemberReviewerId] = useState("");
  const [memberRole, setMemberRole] = useState<ReviewerRole>("Claims Reviewer");
  const [passwordDrafts, setPasswordDrafts] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [addingMember, setAddingMember] = useState(false);
  const creatingRef = useRef(false);
  const addingMemberRef = useRef(false);
  const reviewerMutationRef = useRef(false);
  const memberMutationRef = useRef(false);

  useEffect(() => {
    let mounted = true;
    void fetchAdminReviewers().then((outcome) => {
      if (!mounted) return;
      if (outcome.ok) {
        setReviewers(outcome.reviewers);
        setError(null);
      } else {
        setError(outcome.error);
      }
      setLoading(false);
    });
    void fetchAdminAccountAudit().then((outcome) => {
      if (!mounted) return;
      if (outcome.ok) {
        setAccountAudit(outcome.events);
        setAuditError(null);
      } else {
        setAuditError(outcome.error);
      }
      setAuditLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, [fetchAdminAccountAudit, fetchAdminReviewers]);

  useEffect(() => {
    if (state.dataMode !== "server" || state.caseFile.id === "") {
      setCaseMembers([]);
      setMembersLoading(false);
      return;
    }
    let mounted = true;
    setMembersLoading(true);
    void fetchCaseMembers(state.caseFile.id).then((outcome) => {
      if (!mounted) return;
      if (outcome.ok) {
        setCaseMembers(outcome.members);
        setMemberError(null);
      } else {
        setMemberError(outcome.error);
      }
      setMembersLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, [fetchCaseMembers, state.caseFile.id, state.dataMode]);

  const prependAccountEvent = (event: AccountAuditEvent) => {
    setAccountAudit((current) => [event, ...current].slice(0, 200));
  };

  const handleCreate = async () => {
    if (creatingRef.current || blocker !== null) return;
    creatingRef.current = true;
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      const outcome = await createAdminReviewer({
        name,
        username,
        role,
        initialPassword,
      });
      if (!outcome.ok) {
        setError(outcome.error);
        return;
      }
      setReviewers((current) => upsertReviewer(current, outcome.reviewer));
      prependAccountEvent(outcome.accountAuditEvent);
      setName("");
      setUsername("");
      setRole("Claims Reviewer");
      setInitialPassword("");
      setNotice(`${outcome.reviewer.name} provisioned; password rotation required.`);
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  };

  const handleStatus = async (reviewer: AdminReviewer, isActive: boolean) => {
    if (reviewerMutationRef.current) return;
    reviewerMutationRef.current = true;
    setBusyId(reviewer.id);
    setError(null);
    setNotice(null);
    try {
      const outcome = await setAdminReviewerActive(reviewer.id, isActive);
      if (!outcome.ok) {
        setError(outcome.error);
        return;
      }
      setReviewers((current) => upsertReviewer(current, outcome.reviewer));
      prependAccountEvent(outcome.accountAuditEvent);
      setNotice(`${outcome.reviewer.name} marked ${isActive ? "active" : "inactive"}.`);
    } finally {
      reviewerMutationRef.current = false;
      setBusyId(null);
    }
  };

  const handleResetPassword = async (reviewer: AdminReviewer) => {
    if (reviewerMutationRef.current) return;
    reviewerMutationRef.current = true;
    const nextPassword = passwordDrafts[reviewer.id] ?? "";
    setBusyId(reviewer.id);
    setError(null);
    setNotice(null);
    try {
      const outcome = await resetAdminReviewerPassword(reviewer.id, nextPassword);
      if (!outcome.ok) {
        setError(outcome.error);
        return;
      }
      setReviewers((current) => upsertReviewer(current, outcome.reviewer));
      prependAccountEvent(outcome.accountAuditEvent);
      setPasswordDrafts((current) => ({ ...current, [reviewer.id]: "" }));
      setNotice(`${outcome.reviewer.name} must rotate their password on next sign-in.`);
    } finally {
      reviewerMutationRef.current = false;
      setBusyId(null);
    }
  };

  const handleResetMfa = async (reviewer: AdminReviewer) => {
    if (reviewerMutationRef.current) return;
    const confirmed = window.confirm(
      `Reset MFA for ${reviewer.name}? Every active session will be revoked and the reviewer must enroll a new factor.`,
    );
    if (!confirmed) return;
    reviewerMutationRef.current = true;
    setBusyId(reviewer.id);
    setError(null);
    setNotice(null);
    try {
      const outcome = await resetAdminReviewerMfa(reviewer.id);
      if (!outcome.ok) {
        setError(outcome.error);
        return;
      }
      setReviewers((current) => upsertReviewer(current, outcome.reviewer));
      prependAccountEvent(outcome.accountAuditEvent);
      setNotice(
        `${outcome.reviewer.name}'s MFA factor was reset; sessions were revoked and re-enrollment is required.`,
      );
    } finally {
      reviewerMutationRef.current = false;
      setBusyId(null);
    }
  };

  const handleAddMember = async () => {
    if (addingMemberRef.current || memberMutationRef.current || memberBlocker !== null) {
      return;
    }
    addingMemberRef.current = true;
    memberMutationRef.current = true;
    setAddingMember(true);
    setMemberError(null);
    setMemberNotice(null);
    try {
      const outcome = await addCaseMember({
        caseId: state.caseFile.id,
        reviewerId: memberReviewerId,
        role: memberRole,
      });
      if (!outcome.ok) {
        setMemberError(outcome.error);
        return;
      }
      setCaseMembers((current) => upsertMember(current, outcome.member));
      prependAccountEvent(outcome.accountAuditEvent);
      setMemberReviewerId("");
      setMemberRole("Claims Reviewer");
      setMemberNotice(`${outcome.member.reviewerName} granted access to this matter.`);
    } finally {
      addingMemberRef.current = false;
      memberMutationRef.current = false;
      setAddingMember(false);
    }
  };

  const handleMemberStatus = async (member: AdminCaseMember, isActive: boolean) => {
    if (memberMutationRef.current || addingMemberRef.current) return;
    memberMutationRef.current = true;
    setMemberBusyId(member.reviewerId);
    setMemberError(null);
    setMemberNotice(null);
    try {
      const outcome = await setCaseMemberActive(
        state.caseFile.id,
        member.reviewerId,
        isActive,
      );
      if (!outcome.ok) {
        setMemberError(outcome.error);
        return;
      }
      setCaseMembers((current) => upsertMember(current, outcome.member));
      prependAccountEvent(outcome.accountAuditEvent);
      setMemberNotice(
        `${outcome.member.reviewerName} marked ${isActive ? "active" : "inactive"} for this matter.`,
      );
    } finally {
      memberMutationRef.current = false;
      setMemberBusyId(null);
    }
  };

  const blocker =
    !name.trim() || !username.trim() || initialPassword.length < 10
      ? "Name, username, and an initial password of at least 10 characters are required."
      : null;

  const selectedMemberReviewer = reviewers.find((r) => r.id === memberReviewerId);
  const memberBlocker =
    memberReviewerId === ""
      ? "Select a reviewer before granting matter access."
      : memberRole === "Senior Aviation Counsel" &&
          selectedMemberReviewer?.role !== "Senior Aviation Counsel"
        ? "Only an account-level Senior Aviation Counsel can hold the Senior matter role."
        : null;

  return (
    <div className="view-stack wide">
      <header className="view-header">
        <h2>Reviewer administration</h2>
        <p className="muted">
          Provision reviewers, revoke access, and force password rotation. These
          controls are available only to Senior Aviation Counsel in server mode.
        </p>
      </header>

      <section className="panel" aria-label="Provision reviewer">
        <h3 className="section-title">Provision reviewer</h3>
        <div className="admin-form-grid">
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Username</span>
            <input
              value={username}
              autoComplete="off"
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as ReviewerRole)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Initial password</span>
            <input
              type="password"
              value={initialPassword}
              autoComplete="new-password"
              onChange={(e) => setInitialPassword(e.target.value)}
            />
          </label>
        </div>
        <div className="save-row">
          <button
            type="button"
            className="btn-primary"
            disabled={blocker !== null || creating}
            onClick={() => void handleCreate()}
          >
            {creating ? "Creating…" : "Create reviewer"}
          </button>
          {blocker && <span className="save-blocker">{blocker}</span>}
        </div>
      </section>

      <section className="panel" aria-label="Reviewer accounts">
        <h3 className="section-title">Reviewer accounts</h3>
        {loading ? (
          <p className="muted empty-note">Loading reviewers…</p>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Password</th>
                  <th>MFA</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {reviewers.map((reviewer) => {
                  const isSelf = reviewer.id === state.sessionReviewer?.id;
                  const draft = passwordDrafts[reviewer.id] ?? "";
                  return (
                    <tr key={reviewer.id}>
                      <td>{reviewer.name}</td>
                      <td className="mono">{reviewer.username}</td>
                      <td>{reviewer.role}</td>
                      <td>
                        <span
                          className={`badge ${
                            reviewer.isActive ? "tone-positive" : "tone-negative"
                          }`}
                        >
                          {reviewer.isActive ? "active" : "inactive"}
                        </span>
                      </td>
                      <td>
                        {reviewer.mustChangePassword ? (
                          <span className="badge tone-caution">rotation required</span>
                        ) : (
                          <span className="muted">personalized</span>
                        )}
                      </td>
                      <td>
                        {reviewer.mfaEnabled ? (
                          <span className="badge tone-positive">enabled</span>
                        ) : (
                          <span className="badge tone-caution">enrollment required</span>
                        )}
                      </td>
                      <td>
                        <div className="admin-actions">
                          <button
                            type="button"
                            className="btn-secondary compact"
                            disabled={busyId === reviewer.id || isSelf}
                            onClick={() => void handleStatus(reviewer, !reviewer.isActive)}
                          >
                            {reviewer.isActive ? "Deactivate" : "Reactivate"}
                          </button>
                          <input
                            type="password"
                            aria-label={`New password for ${reviewer.name}`}
                            value={draft}
                            placeholder="New password"
                            autoComplete="new-password"
                            onChange={(e) =>
                              setPasswordDrafts((current) => ({
                                ...current,
                                [reviewer.id]: e.target.value,
                              }))
                            }
                          />
                          <button
                            type="button"
                            className="btn-secondary compact"
                            disabled={busyId === reviewer.id || draft.length < 10}
                            onClick={() => void handleResetPassword(reviewer)}
                          >
                            Reset password
                          </button>
                          <button
                            type="button"
                            className="btn-secondary compact"
                            aria-label={`Reset MFA for ${reviewer.name}`}
                            title={
                              isSelf
                                ? "A second Senior Aviation Counsel must reset your MFA."
                                : reviewer.mfaEnabled
                                  ? "Revoke sessions and require MFA re-enrollment."
                                  : "This reviewer has not enrolled MFA."
                            }
                            disabled={
                              busyId === reviewer.id || isSelf || !reviewer.mfaEnabled
                            }
                            onClick={() => void handleResetMfa(reviewer)}
                          >
                            Reset MFA
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {notice && (
          <p className="save-confirm" role="status">
            {notice}
          </p>
        )}
        {error && <p className="form-error">{error}</p>}
      </section>

      {state.caseFile.id !== "" && (
      <section className="panel" aria-label="Matter access">
        <h3 className="section-title">Matter access</h3>
        <p className="muted">
          Manage reviewer membership for {state.caseFile.name}. Matter access is
          separate from account activation.
        </p>
        <div className="matter-admin-grid">
          <label className="field">
            <span>Reviewer</span>
            <select
              value={memberReviewerId}
              onChange={(event) => setMemberReviewerId(event.target.value)}
            >
              <option value="">Select reviewer</option>
              {reviewers.map((reviewer) => (
                <option key={reviewer.id} value={reviewer.id}>
                  {reviewer.name} — {reviewer.role}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Matter role</span>
            <select
              value={memberRole}
              onChange={(event) => setMemberRole(event.target.value as ReviewerRole)}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="save-row">
          <button
            type="button"
            className="btn-primary"
            disabled={memberBlocker !== null || addingMember}
            onClick={() => void handleAddMember()}
          >
            {addingMember ? "Granting…" : "Grant access"}
          </button>
          {memberBlocker && <span className="save-blocker">{memberBlocker}</span>}
        </div>

        {membersLoading ? (
          <p className="muted empty-note">Loading matter members…</p>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Reviewer</th>
                  <th>Username</th>
                  <th>Account role</th>
                  <th>Matter role</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {caseMembers.map((member) => {
                  const isSelf = member.reviewerId === state.sessionReviewer?.id;
                  return (
                    <tr key={member.reviewerId}>
                      <td>{member.reviewerName}</td>
                      <td className="mono">{member.username}</td>
                      <td>{member.reviewerRole}</td>
                      <td>{member.role}</td>
                      <td>
                        <span
                          className={`badge ${
                            member.isActive ? "tone-positive" : "tone-negative"
                          }`}
                        >
                          {member.isActive ? "active" : "inactive"}
                        </span>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn-secondary compact"
                          disabled={memberBusyId === member.reviewerId || isSelf}
                          onClick={() => void handleMemberStatus(member, !member.isActive)}
                        >
                          {member.isActive ? "Suspend access" : "Restore access"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {memberNotice && (
          <p className="save-confirm" role="status">
            {memberNotice}
          </p>
        )}
        {memberError && <p className="form-error">{memberError}</p>}
      </section>
      )}

      <section className="panel" aria-label="Account audit">
        <h3 className="section-title">Account audit</h3>
        {auditLoading ? (
          <p className="muted empty-note">Loading reviewer account events…</p>
        ) : auditError ? (
          <p className="form-error" role="alert">
            Account audit unavailable: {auditError}
          </p>
        ) : accountAudit.length === 0 ? (
          <p className="muted empty-note">No reviewer account events recorded.</p>
        ) : (
          <ul className="account-audit-list">
            {accountAudit.map((event) => (
              <li key={event.id} className="account-audit-item">
                <span className="mono muted">{event.at}</span>
                <strong>{event.action}</strong>
                <span className="muted">
                  {event.actor} → {event.detail}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
