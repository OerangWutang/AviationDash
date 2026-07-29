import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import type {
  AuditEvent,
  CaseFile,
  Claim,
  Conflict,
  ReviewDecision,
  Reviewer,
  ReviewerRole,
  ReportSection,
  SourceDocument,
} from "../domain/types";
import {
  applyDecision,
  validateDecision,
  type DecisionInput,
} from "../domain/decisions";
import {
  createClaim,
  flagConflict,
  validateFlagConflict,
  validateNewClaim,
  type FlagConflictInput,
  type NewClaimInput,
} from "../domain/claims";
import {
  createReportSection,
  updateReportSection,
  validateReportSection,
  type ReportSectionInput,
} from "../domain/reportSections";
import { computeSectionImpact } from "../domain/report";
import type { NewMatterInput } from "../domain/matters";
import * as sample from "../data/sampleCase";
import * as api from "../api/client";
import {
  clearSnapshot,
  loadSnapshot,
  saveSnapshot,
  toSnapshot,
} from "./persistence";

export type View =
  | "conflicts"
  | "claims"
  | "documents"
  | "reports"
  | "audit"
  | "export"
  | "admin";

/** Where the case record lives: this browser (localStorage working copy) or
 *  the Atlas Argus backend (server-authoritative). */
export type DataMode = "local" | "server";

export interface AppState {
  dataMode: DataMode;
  bootStatus: "loading" | "unauthenticated" | "ready" | "error";
  bootError: string | null;
  /** Authenticated identity (server mode only). */
  sessionReviewer: Reviewer | null;
  /** Absolute server-provided expiry for the authenticated session. */
  sessionExpiresAt: string | null;
  /** Matter membership for the loaded case (server mode only). */
  caseMembership: api.CaseMembership | null;
  /** Cases the authenticated reviewer may access (server mode only). */
  availableCases: api.CaseSummary[];
  /** MFA state for the current server session. */
  mfa: api.MfaStatus | null;
  mfaGate: boolean;
  /** "forced" = must rotate before working; "voluntary" = user-requested. */
  passwordGate: "forced" | "voluntary" | null;
  caseFile: CaseFile;
  reviewers: Reviewer[];
  sources: Map<string, SourceDocument>;
  claims: Map<string, Claim>;
  conflicts: Map<string, Conflict>;
  conflictOrder: string[];
  decisions: Map<string, ReviewDecision>;
  /** Newest first. */
  auditEvents: AuditEvent[];
  reportSections: ReportSection[];
  view: View;
  selectedConflictId: string;
  /** Claim focused in the Evidence Rail; falls back to the conflict's Claim A. */
  selectedClaimId: string | null;
  activeReviewerId: string;
  lastSavedDecisionId: string | null;
}

type Action =
  | { type: "select_view"; view: View }
  | { type: "select_conflict"; conflictId: string }
  | { type: "select_claim"; claimId: string }
  | { type: "set_reviewer"; reviewerId: string }
  | {
      type: "decision_applied";
      conflict: Conflict;
      claims: Claim[];
      decision: ReviewDecision;
      auditEvents: AuditEvent[];
    }
  | { type: "audit_appended"; event: AuditEvent }
  | { type: "claim_created"; claim: Claim; auditEvent: AuditEvent }
  | {
      type: "conflict_flagged";
      conflict: Conflict;
      claims: Claim[];
      auditEvents: AuditEvent[];
    }
  | { type: "section_saved"; section: ReportSection; auditEvent: AuditEvent }
  | { type: "case_loaded"; payload: api.CasePayload }
  | { type: "case_refreshed"; payload: api.CasePayload }
  | { type: "cases_loaded"; cases: api.CaseSummary[] }
  | { type: "boot_loading" }
  | { type: "boot_failed"; error: string }
  | {
      type: "session_established";
      reviewer: Reviewer;
      mustChangePassword: boolean;
      mfa: api.MfaStatus;
      expiresAt: string;
    }
  | {
      type: "session_revalidated";
      reviewer: Reviewer;
      mustChangePassword: boolean;
      mfa: api.MfaStatus;
      expiresAt: string;
    }
  | { type: "session_cleared" }
  | { type: "password_gate"; gate: "voluntary" | null }
  | { type: "mfa_gate"; open: boolean }
  | { type: "mfa_status"; status: api.MfaStatus }
  | { type: "password_changed" }
  | { type: "reviewer_admin_changed"; reviewer: api.AdminReviewer }
  | { type: "case_reset" };

const EMPTY_CASE_FILE: CaseFile = {
  id: "",
  name: "",
  aircraft: "",
  accidentDate: "",
  location: "",
  matterType: "",
  status: "",
  docketRef: "",
};

function sessionExpiryInvalidOrElapsed(expiresAt: string): boolean {
  const parsed = Date.parse(expiresAt);
  return !Number.isFinite(parsed) || parsed <= Date.now();
}

function seedState(): AppState {
  const byAtDesc = (a: AuditEvent, b: AuditEvent) => b.at.localeCompare(a.at);
  return {
    dataMode: "local",
    bootStatus: "ready",
    bootError: null,
    sessionReviewer: null,
    sessionExpiresAt: null,
    caseMembership: null,
    availableCases: [],
    mfa: null,
    mfaGate: false,
    passwordGate: null,
    caseFile: sample.caseFile,
    reviewers: sample.reviewers,
    sources: new Map(sample.sources.map((s) => [s.id, s])),
    claims: new Map(sample.claims.map((c) => [c.id, c])),
    conflicts: new Map(sample.conflicts.map((c) => [c.id, c])),
    conflictOrder: sample.conflicts.map((c) => c.id),
    decisions: new Map(sample.decisions.map((d) => [d.id, d])),
    auditEvents: [...sample.auditEvents].sort(byAtDesc),
    reportSections: sample.reportSections,
    view: "conflicts",
    selectedConflictId: sample.conflicts[0].id,
    selectedClaimId: null,
    activeReviewerId: sample.reviewers[0].id,
    lastSavedDecisionId: null,
  };
}

/** Local mode: seed overlaid with this browser's saved working copy.
 *  Server mode: seed shapes as a placeholder behind the boot screen. */
function initialState(mode: DataMode): AppState {
  const seed = seedState();
  if (mode === "server") {
    return { ...seed, dataMode: "server", bootStatus: "loading" };
  }
  const snapshot = loadSnapshot();
  if (!snapshot) return seed;
  return {
    ...seed,
    claims: new Map(snapshot.claims.map((c) => [c.id, c])),
    conflicts: new Map(snapshot.conflicts.map((c) => [c.id, c])),
    conflictOrder: snapshot.conflictOrder,
    decisions: new Map(snapshot.decisions.map((d) => [d.id, d])),
    auditEvents: snapshot.auditEvents,
    reportSections: snapshot.reportSections ?? seed.reportSections,
    selectedConflictId: snapshot.conflictOrder[0] ?? seed.selectedConflictId,
    activeReviewerId: seed.reviewers.some((r) => r.id === snapshot.activeReviewerId)
      ? snapshot.activeReviewerId
      : seed.activeReviewerId,
  };
}

function reducer(state: AppState, action: Action): AppState {
  // An async request that began before logout/revocation must never restore
  // matter data after the authenticated state has been scrubbed.
  if (
    state.dataMode === "server" &&
    state.sessionReviewer === null &&
    (action.type === "decision_applied" ||
      action.type === "audit_appended" ||
      action.type === "claim_created" ||
      action.type === "conflict_flagged" ||
      action.type === "section_saved" ||
      action.type === "case_loaded" ||
      action.type === "case_refreshed" ||
      action.type === "cases_loaded" ||
      action.type === "reviewer_admin_changed")
  ) {
    return state;
  }
  switch (action.type) {
    case "select_view":
      return { ...state, view: action.view };
    case "select_conflict":
      return {
        ...state,
        view: "conflicts",
        selectedConflictId: action.conflictId,
        selectedClaimId: null,
        lastSavedDecisionId: null,
      };
    case "select_claim":
      return { ...state, selectedClaimId: action.claimId };
    case "set_reviewer":
      return { ...state, activeReviewerId: action.reviewerId };
    case "decision_applied": {
      const conflicts = new Map(state.conflicts);
      conflicts.set(action.conflict.id, action.conflict);
      const claims = new Map(state.claims);
      for (const claim of action.claims) {
        claims.set(claim.id, claim);
      }
      const decisions = new Map(state.decisions);
      decisions.set(action.decision.id, action.decision);
      return {
        ...state,
        conflicts,
        claims,
        decisions,
        auditEvents: [...action.auditEvents, ...state.auditEvents],
        lastSavedDecisionId: action.decision.id,
      };
    }
    case "audit_appended":
      return { ...state, auditEvents: [action.event, ...state.auditEvents] };
    case "claim_created": {
      const claims = new Map(state.claims);
      claims.set(action.claim.id, action.claim);
      return {
        ...state,
        claims,
        auditEvents: [action.auditEvent, ...state.auditEvents],
      };
    }
    case "conflict_flagged": {
      const conflicts = new Map(state.conflicts);
      conflicts.set(action.conflict.id, action.conflict);
      const claims = new Map(state.claims);
      for (const claim of action.claims) {
        claims.set(claim.id, claim);
      }
      return {
        ...state,
        conflicts,
        conflictOrder: [...state.conflictOrder, action.conflict.id],
        claims,
        auditEvents: [...action.auditEvents, ...state.auditEvents],
        // Jump straight to the new conflict in the review workspace.
        view: "conflicts",
        selectedConflictId: action.conflict.id,
        selectedClaimId: null,
        lastSavedDecisionId: null,
      };
    }
    case "section_saved": {
      const exists = state.reportSections.some((s) => s.id === action.section.id);
      return {
        ...state,
        reportSections: exists
          ? state.reportSections.map((s) =>
              s.id === action.section.id ? action.section : s,
            )
          : [...state.reportSections, action.section],
        auditEvents: [action.auditEvent, ...state.auditEvents],
      };
    }
    case "case_loaded": {
      const { payload } = action;
      return {
        ...state,
        bootStatus: "ready",
        bootError: null,
        caseFile: payload.caseFile,
        caseMembership: payload.caseMembership,
        reviewers: payload.reviewers,
        sources: new Map(payload.sources.map((s) => [s.id, s])),
        claims: new Map(payload.claims.map((c) => [c.id, c])),
        conflicts: new Map(payload.conflicts.map((c) => [c.id, c])),
        conflictOrder: payload.conflictOrder,
        decisions: new Map(payload.decisions.map((d) => [d.id, d])),
        auditEvents: payload.auditEvents,
        reportSections: payload.reportSections,
        view: "conflicts",
        selectedConflictId: payload.conflictOrder[0] ?? "",
        selectedClaimId: null,
        lastSavedDecisionId: null,
        activeReviewerId:
          state.sessionReviewer !== null &&
          payload.reviewers.some((r) => r.id === state.sessionReviewer!.id)
            ? state.sessionReviewer.id
            : (payload.reviewers[0]?.id ?? ""),
      };
    }
    case "case_refreshed": {
      const { payload } = action;
      const conflictIds = new Set(payload.conflictOrder);
      const claimIds = new Set(payload.claims.map((claim) => claim.id));
      return {
        ...state,
        bootStatus: "ready",
        bootError: null,
        caseFile: payload.caseFile,
        caseMembership: payload.caseMembership,
        reviewers: payload.reviewers,
        sources: new Map(payload.sources.map((source) => [source.id, source])),
        claims: new Map(payload.claims.map((claim) => [claim.id, claim])),
        conflicts: new Map(payload.conflicts.map((conflict) => [conflict.id, conflict])),
        conflictOrder: payload.conflictOrder,
        decisions: new Map(payload.decisions.map((decision) => [decision.id, decision])),
        auditEvents: payload.auditEvents,
        reportSections: payload.reportSections,
        selectedConflictId: conflictIds.has(state.selectedConflictId)
          ? state.selectedConflictId
          : (payload.conflictOrder[0] ?? ""),
        selectedClaimId:
          state.selectedClaimId !== null && claimIds.has(state.selectedClaimId)
            ? state.selectedClaimId
            : null,
        activeReviewerId:
          state.sessionReviewer !== null &&
          payload.reviewers.some((reviewer) => reviewer.id === state.sessionReviewer!.id)
            ? state.sessionReviewer.id
            : (payload.reviewers[0]?.id ?? ""),
        lastSavedDecisionId: null,
      };
    }
    case "cases_loaded":
      return { ...state, availableCases: action.cases };
    case "boot_loading":
      return { ...state, bootStatus: "loading", bootError: null };
    case "boot_failed":
      return { ...state, bootStatus: "error", bootError: action.error };
    case "session_established":
      return {
        ...state,
        bootStatus: action.mustChangePassword ? "ready" : state.bootStatus,
        bootError: null,
        sessionReviewer: action.reviewer,
        sessionExpiresAt: action.expiresAt,
        activeReviewerId: action.reviewer.id,
        mfa: action.mfa,
        passwordGate: action.mustChangePassword ? "forced" : null,
      };
    case "session_revalidated":
      return {
        ...state,
        bootStatus: action.mustChangePassword ? "ready" : state.bootStatus,
        sessionReviewer: action.reviewer,
        sessionExpiresAt: action.expiresAt,
        activeReviewerId: action.reviewer.id,
        mfa: action.mfa,
        passwordGate: action.mustChangePassword ? "forced" : state.passwordGate,
      };
    case "session_cleared":
      return {
        ...state,
        sessionReviewer: null,
        sessionExpiresAt: null,
        caseMembership: null,
        availableCases: [],
        mfa: null,
        mfaGate: false,
        bootStatus: "unauthenticated",
        bootError: null,
        passwordGate: null,
        caseFile: EMPTY_CASE_FILE,
        reviewers: [],
        sources: new Map(),
        claims: new Map(),
        conflicts: new Map(),
        conflictOrder: [],
        decisions: new Map(),
        auditEvents: [],
        reportSections: [],
        view: "conflicts",
        selectedConflictId: "",
        selectedClaimId: null,
        activeReviewerId: "",
        lastSavedDecisionId: null,
      };
    case "password_gate":
      return { ...state, passwordGate: action.gate };
    case "mfa_gate":
      return { ...state, mfaGate: action.open };
    case "mfa_status":
      return { ...state, mfa: action.status, mfaGate: !action.status.verified };
    case "password_changed":
      return { ...state, passwordGate: null };
    case "reviewer_admin_changed": {
      const reviewer: Reviewer = {
        id: action.reviewer.id,
        name: action.reviewer.name,
        role: action.reviewer.role,
      };
      const exists = state.reviewers.some((r) => r.id === reviewer.id);
      return {
        ...state,
        reviewers: (exists
          ? state.reviewers.map((r) => (r.id === reviewer.id ? reviewer : r))
          : [...state.reviewers, reviewer]
        ).sort((a, b) => a.name.localeCompare(b.name)),
      };
    }
    case "case_reset":
      return seedState();
  }
}

export type SaveDecisionOutcome =
  | { ok: true; decision: ReviewDecision }
  | { ok: false; error: string };

export type CreateClaimOutcome =
  | { ok: true; claim: Claim }
  | { ok: false; error: string };

export type FlagConflictOutcome =
  | { ok: true; conflict: Conflict }
  | { ok: false; error: string };

export type SaveSectionOutcome =
  | { ok: true; section: ReportSection }
  | { ok: false; error: string };

export type SimpleOutcome = { ok: true } | { ok: false; error: string };

export type AdminReviewersOutcome =
  | { ok: true; reviewers: api.AdminReviewer[] }
  | { ok: false; error: string };

export type AdminReviewerOutcome =
  | { ok: true; reviewer: api.AdminReviewer; accountAuditEvent: api.AccountAuditEvent }
  | { ok: false; error: string };

export type AdminCaseMembersOutcome =
  | { ok: true; members: api.AdminCaseMember[] }
  | { ok: false; error: string };

export type AdminCaseMemberOutcome =
  | { ok: true; member: api.AdminCaseMember; accountAuditEvent: api.AccountAuditEvent }
  | { ok: false; error: string };

export type PacketArtifactListOutcome =
  | { ok: true; result: api.PacketArtifactList }
  | { ok: false; error: string };

export type PacketArtifactDetailOutcome =
  | { ok: true; packet: api.PacketArtifactDetail }
  | { ok: false; error: string };

export interface PacketExportRecord {
  packetId: string;
  packetType: string;
  included: number;
  excluded: number;
  withheld: number;
  sha256: string;
}

interface SessionOperationScope {
  sessionEpoch: number;
  reviewerId: string;
}

interface CaseOperationScope extends SessionOperationScope {
  caseEpoch: number;
  caseId: string;
}

type OperationScope = SessionOperationScope | CaseOperationScope;

const SUPERSEDED_OPERATION_ERROR =
  "This operation was superseded by a session or matter change.";

interface StoreValue {
  state: AppState;
  selectView: (view: View) => void;
  selectConflict: (conflictId: string) => void;
  selectClaim: (claimId: string) => void;
  setReviewer: (reviewerId: string) => void;
  retryBoot: () => void;
  /** Server mode: authenticate and load the case. */
  login: (username: string, password: string) => Promise<SimpleOutcome>;
  /** Server mode: load another matter the reviewer is assigned to. */
  switchCase: (caseId: string) => Promise<SimpleOutcome>;
  /** Server mode: open a new matter and switch to it. */
  createMatter: (input: NewMatterInput) => Promise<SimpleOutcome>;
  /** Server mode: revoke the session and return to the sign-in screen. */
  logout: () => Promise<void>;
  /** Server mode: rotate the password (revokes every other session). */
  changePassword: (currentPassword: string, newPassword: string) => Promise<SimpleOutcome>;
  /** Open/close the voluntary password-change screen. */
  requestPasswordChange: () => void;
  dismissPasswordChange: () => void;
  requestMfa: () => void;
  dismissMfa: () => void;
  startMfaEnrollment: () => Promise<
    | { ok: true; enrollment: api.MfaEnrollment }
    | { ok: false; error: string }
  >;
  enableMfa: (code: string) => Promise<SimpleOutcome>;
  verifyMfa: (code: string) => Promise<SimpleOutcome>;
  saveDecision: (
    input: Omit<DecisionInput, "reviewerName" | "reviewerRole">,
  ) => Promise<SaveDecisionOutcome>;
  createClaim: (input: NewClaimInput) => Promise<CreateClaimOutcome>;
  flagConflict: (input: FlagConflictInput) => Promise<FlagConflictOutcome>;
  /** Draft a new report section, or revise `sectionId` if given. */
  saveReportSection: (
    input: ReportSectionInput,
    sectionId?: string,
  ) => Promise<SaveSectionOutcome>;
  /** Server mode: approve the section's current revision for production. */
  approveReportSection: (sectionId: string) => Promise<SaveSectionOutcome>;
  /** Local mode: log a client-generated packet to the audit trail. */
  recordPacketExport: (record: PacketExportRecord) => Promise<SimpleOutcome>;
  /** Server mode: the backend builds, redacts, and hash-stamps the packet. */
  generateServerPacket: (
    packetType: string,
  ) => Promise<{ ok: true; packet: api.ServerPacket } | { ok: false; error: string }>;
  /** Server mode: list and retrieve exact persisted packet artifacts. */
  fetchPacketArtifacts: (input?: {
    limit?: number;
    offset?: number;
  }) => Promise<PacketArtifactListOutcome>;
  fetchPacketArtifact: (packetId: string) => Promise<PacketArtifactDetailOutcome>;
  /** Server mode: Senior Aviation Counsel reviewer administration. */
  fetchAdminReviewers: () => Promise<AdminReviewersOutcome>;
  fetchAdminAccountAudit: () => Promise<
    | { ok: true; events: api.AccountAuditEvent[] }
    | { ok: false; error: string }
  >;
  createAdminReviewer: (input: {
    name: string;
    username: string;
    role: string;
    initialPassword: string;
  }) => Promise<AdminReviewerOutcome>;
  setAdminReviewerActive: (
    reviewerId: string,
    isActive: boolean,
  ) => Promise<AdminReviewerOutcome>;
  resetAdminReviewerPassword: (
    reviewerId: string,
    newPassword: string,
  ) => Promise<AdminReviewerOutcome>;
  /** Clear another reviewer's lost factor, revoke sessions, and require re-enrollment. */
  resetAdminReviewerMfa: (reviewerId: string) => Promise<AdminReviewerOutcome>;
  /** Server mode: Senior Aviation Counsel matter membership administration. */
  fetchCaseMembers: (caseId: string) => Promise<AdminCaseMembersOutcome>;
  addCaseMember: (input: {
    caseId: string;
    reviewerId: string;
    role: string;
  }) => Promise<AdminCaseMemberOutcome>;
  setCaseMemberActive: (
    caseId: string,
    reviewerId: string,
    isActive: boolean,
  ) => Promise<AdminCaseMemberOutcome>;
  /** Local mode only: discard browser working copy and restore the sample case. */
  resetCase: () => Promise<void>;
}

const StoreContext = createContext<StoreValue | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const mode: DataMode = api.apiEnabled() ? "server" : "local";
  const [state, dispatch] = useReducer(reducer, mode, initialState);

  // Ids and timestamps are generated here (not in the reducer) so the reducer
  // stays pure under React StrictMode's double-invocation.
  const stateRef = useRef(state);
  stateRef.current = state;

  // Async server work is scoped twice: sessions invalidate all prior work,
  // while each matter-load request invalidates work tied to the prior matter.
  // Refs are advanced synchronously so correctness does not depend on React
  // finishing a render before a network response settles.
  const authAttemptEpochRef = useRef(0);
  const logoutInFlightRef = useRef(false);
  const sessionEpochRef = useRef(0);
  const sessionReviewerIdRef = useRef<string | null>(null);
  const caseEpochRef = useRef(0);
  const currentCaseIdRef = useRef<string | null>(null);

  const beginSession = useCallback((reviewerId: string): SessionOperationScope => {
    sessionEpochRef.current += 1;
    sessionReviewerIdRef.current = reviewerId;
    caseEpochRef.current += 1;
    currentCaseIdRef.current = null;
    return { sessionEpoch: sessionEpochRef.current, reviewerId };
  }, []);

  const clearServerSession = useCallback(() => {
    authAttemptEpochRef.current += 1;
    sessionEpochRef.current += 1;
    sessionReviewerIdRef.current = null;
    caseEpochRef.current += 1;
    currentCaseIdRef.current = null;
    dispatch({ type: "session_cleared" });
  }, []);

  const captureSessionScope = useCallback((): SessionOperationScope | null => {
    const reviewerId = sessionReviewerIdRef.current;
    return reviewerId === null
      ? null
      : { sessionEpoch: sessionEpochRef.current, reviewerId };
  }, []);

  const isSessionScopeCurrent = useCallback((scope: SessionOperationScope): boolean => {
    return (
      sessionEpochRef.current === scope.sessionEpoch &&
      sessionReviewerIdRef.current === scope.reviewerId
    );
  }, []);

  const isCaseScopeCurrent = useCallback(
    (scope: CaseOperationScope): boolean =>
      isSessionScopeCurrent(scope) &&
      caseEpochRef.current === scope.caseEpoch &&
      currentCaseIdRef.current === scope.caseId,
    [isSessionScopeCurrent],
  );

  const isOperationScopeCurrent = useCallback(
    (scope: OperationScope): boolean =>
      "caseEpoch" in scope
        ? isCaseScopeCurrent(scope)
        : isSessionScopeCurrent(scope),
    [isCaseScopeCurrent, isSessionScopeCurrent],
  );

  const captureCaseScope = useCallback(
    (caseId: string): CaseOperationScope | null => {
      const sessionScope = captureSessionScope();
      if (
        sessionScope === null ||
        caseId.length === 0 ||
        currentCaseIdRef.current !== caseId
      ) {
        return null;
      }
      return {
        ...sessionScope,
        caseEpoch: caseEpochRef.current,
        caseId,
      };
    },
    [captureSessionScope],
  );

  const beginCaseRequest = useCallback(
    (caseId: string, sessionScope: SessionOperationScope): CaseOperationScope | null => {
      if (!isSessionScopeCurrent(sessionScope)) return null;
      caseEpochRef.current += 1;
      currentCaseIdRef.current = caseId;
      return {
        ...sessionScope,
        caseEpoch: caseEpochRef.current,
        caseId,
      };
    },
    [isSessionScopeCurrent],
  );

  const supersededFailure = useCallback(
    (): { ok: false; error: string } => ({
      ok: false,
      error: SUPERSEDED_OPERATION_ERROR,
    }),
    [],
  );

  const loadCases = useCallback(
    async (scope: SessionOperationScope): Promise<api.CaseSummary[] | null> => {
      try {
        const result = await api.fetchCases();
        if (!isSessionScopeCurrent(scope)) return null;
        dispatch({ type: "cases_loaded", cases: result.cases });
        return result.cases;
      } catch (error) {
        if (!isSessionScopeCurrent(scope)) return null;
        throw error;
      }
    },
    [isSessionScopeCurrent],
  );

  const loadCase = useCallback(
    async (
      caseId: string | undefined,
      sessionScope: SessionOperationScope,
    ): Promise<SimpleOutcome> => {
      const requestScope = beginCaseRequest(caseId ?? "", sessionScope);
      if (requestScope === null) return supersededFailure();
      try {
        const payload = caseId ? await api.fetchCaseById(caseId) : await api.fetchCase();
        if (!isCaseScopeCurrent(requestScope)) return supersededFailure();
        if (caseId && payload.caseFile.id !== caseId) {
          return { ok: false, error: "The case service returned the wrong matter." };
        }
        currentCaseIdRef.current = payload.caseFile.id;
        dispatch({ type: "case_loaded", payload });
        return { ok: true };
      } catch (error) {
        if (!isCaseScopeCurrent(requestScope)) return supersededFailure();
        if (api.isUnauthorized(error)) {
          clearServerSession();
          return { ok: false, error: "Your session has expired — sign in again." };
        }
        dispatch({ type: "boot_failed", error: api.errorMessage(error) });
        return { ok: false, error: api.errorMessage(error) };
      }
    },
    [
      beginCaseRequest,
      clearServerSession,
      isCaseScopeCurrent,
      supersededFailure,
    ],
  );

  const bootFromServer = useCallback(async () => {
    const authAttempt = ++authAttemptEpochRef.current;
    let sessionScope: SessionOperationScope | null = null;
    dispatch({ type: "boot_loading" });
    try {
      const { reviewer, mustChangePassword, mfa, expiresAt } = await api.fetchSession();
      if (authAttemptEpochRef.current !== authAttempt) return;
      if (sessionExpiryInvalidOrElapsed(expiresAt)) {
        clearServerSession();
        return;
      }
      sessionScope = beginSession(reviewer.id);
      dispatch({
        type: "session_established",
        reviewer,
        mustChangePassword,
        mfa,
        expiresAt,
      });
      // Password rotation is an authentication-only state. Matter endpoints
      // intentionally remain untouched until the required rotation succeeds.
      if (mustChangePassword) return;
    } catch (error) {
      if (authAttemptEpochRef.current !== authAttempt) return;
      if (api.isUnauthorized(error)) {
        clearServerSession();
      } else {
        dispatch({ type: "boot_failed", error: api.errorMessage(error) });
      }
      return;
    }
    try {
      if (sessionScope === null) return;
      const cases = await loadCases(sessionScope);
      if (cases === null) return;
      await loadCase(cases[0]?.caseFile.id, sessionScope);
    } catch (error) {
      if (sessionScope === null || !isSessionScopeCurrent(sessionScope)) return;
      if (api.isUnauthorized(error)) {
        clearServerSession();
      } else {
        dispatch({ type: "boot_failed", error: api.errorMessage(error) });
      }
    }
  }, [beginSession, clearServerSession, isSessionScopeCurrent, loadCase, loadCases]);

  useEffect(() => {
    if (mode === "server") {
      void bootFromServer();
    }
  }, [mode, bootFromServer]);

  // Fail closed at the server-provided expiry even if the tab remains idle.
  useEffect(() => {
    if (mode !== "server" || state.sessionReviewer === null || !state.sessionExpiresAt) {
      return;
    }
    const scope = captureSessionScope();
    if (scope === null) return;
    const expiresAt = Date.parse(state.sessionExpiresAt);
    if (!Number.isFinite(expiresAt)) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      if (isSessionScopeCurrent(scope)) clearServerSession();
      return;
    }
    const timer = window.setTimeout(
      () => {
        if (isSessionScopeCurrent(scope)) clearServerSession();
      },
      Math.min(remaining, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [
    captureSessionScope,
    clearServerSession,
    isSessionScopeCurrent,
    mode,
    state.sessionExpiresAt,
    state.sessionReviewer,
  ]);

  const revalidationInFlight = useRef<number | null>(null);
  const revalidateServerSession = useCallback(async () => {
    const current = stateRef.current;
    if (
      current.dataMode !== "server" ||
      current.bootStatus !== "ready" ||
      current.sessionReviewer === null ||
      current.passwordGate !== null ||
      current.mfaGate
    ) {
      return;
    }
    const scope = captureSessionScope();
    if (scope === null) return;
    if (revalidationInFlight.current === scope.sessionEpoch) return;
    revalidationInFlight.current = scope.sessionEpoch;
    try {
      const { reviewer, mustChangePassword, mfa, expiresAt } = await api.fetchSession();
      if (!isSessionScopeCurrent(scope)) return;
      if (sessionExpiryInvalidOrElapsed(expiresAt)) {
        clearServerSession();
        return;
      }
      if (reviewer.id !== scope.reviewerId) {
        clearServerSession();
        return;
      }
      dispatch({
        type: "session_revalidated",
        reviewer,
        mustChangePassword,
        mfa,
        expiresAt,
      });
    } catch (error) {
      if (isSessionScopeCurrent(scope) && api.isUnauthorized(error)) {
        clearServerSession();
      }
      // A transient connectivity failure does not manufacture a logout. The
      // absolute expiry timer above remains authoritative while offline.
    } finally {
      if (revalidationInFlight.current === scope.sessionEpoch) {
        revalidationInFlight.current = null;
      }
    }
  }, [captureSessionScope, clearServerSession, isSessionScopeCurrent]);

  useEffect(() => {
    if (mode !== "server") return;
    const revalidateWhenActive = () => {
      if (document.visibilityState === "visible") {
        void revalidateServerSession();
      }
    };
    window.addEventListener("focus", revalidateWhenActive);
    window.addEventListener("pageshow", revalidateWhenActive);
    document.addEventListener("visibilitychange", revalidateWhenActive);
    const interval = window.setInterval(() => void revalidateServerSession(), 5 * 60_000);
    return () => {
      window.removeEventListener("focus", revalidateWhenActive);
      window.removeEventListener("pageshow", revalidateWhenActive);
      document.removeEventListener("visibilitychange", revalidateWhenActive);
      window.clearInterval(interval);
    };
  }, [mode, revalidateServerSession]);

  const login = useCallback(
    async (username: string, password: string): Promise<SimpleOutcome> => {
      if (logoutInFlightRef.current) {
        return { ok: false, error: "Sign-out is still in progress." };
      }
      const authAttempt = ++authAttemptEpochRef.current;
      let sessionScope: SessionOperationScope | null = null;
      try {
        const { reviewer, mustChangePassword, mfa, expiresAt } = await api.login({
          username,
          password,
        });
        if (authAttemptEpochRef.current !== authAttempt) return supersededFailure();
        if (sessionExpiryInvalidOrElapsed(expiresAt)) {
          clearServerSession();
          return { ok: false, error: "The server returned an expired session. Sign in again." };
        }
        sessionScope = beginSession(reviewer.id);
        dispatch({
          type: "session_established",
          reviewer,
          mustChangePassword,
          mfa,
          expiresAt,
        });
        if (mustChangePassword) return { ok: true };
      } catch (error) {
        if (authAttemptEpochRef.current !== authAttempt) return supersededFailure();
        return { ok: false, error: api.errorMessage(error) };
      }
      dispatch({ type: "boot_loading" });
      try {
        if (sessionScope === null) return supersededFailure();
        const cases = await loadCases(sessionScope);
        if (cases === null) return supersededFailure();
        return await loadCase(cases[0]?.caseFile.id, sessionScope);
      } catch (error) {
        if (sessionScope === null || !isSessionScopeCurrent(sessionScope)) {
          return supersededFailure();
        }
        if (api.isUnauthorized(error)) {
          clearServerSession();
          return { ok: false, error: "Your session has expired — sign in again." };
        }
        dispatch({ type: "boot_failed", error: api.errorMessage(error) });
        return { ok: false, error: api.errorMessage(error) };
      }
    },
    [
      beginSession,
      clearServerSession,
      isSessionScopeCurrent,
      loadCase,
      loadCases,
      supersededFailure,
    ],
  );

  const switchCase = useCallback(
    async (caseId: string): Promise<SimpleOutcome> => {
      if (stateRef.current.dataMode !== "server") {
        return { ok: false, error: "Matter switching is a server-mode capability." };
      }
      const sessionScope = captureSessionScope();
      if (sessionScope === null) return supersededFailure();
      dispatch({ type: "boot_loading" });
      return await loadCase(caseId, sessionScope);
    },
    [captureSessionScope, loadCase, supersededFailure],
  );

  const createMatter = useCallback(
    async (input: NewMatterInput): Promise<SimpleOutcome> => {
      if (stateRef.current.dataMode !== "server") {
        return { ok: false, error: "Opening a matter is a server-mode capability." };
      }
      const sessionScope = captureSessionScope();
      if (sessionScope === null) return supersededFailure();
      try {
        const created = await api.postCase(input);
        if (!isSessionScopeCurrent(sessionScope)) return supersededFailure();
        // Refresh the matter list before switching. Without this the reviewer
        // lands in the new matter while `availableCases` still holds only the
        // old one, so the matter picker (which appears only for more than one)
        // stays hidden and there is no way back short of a page reload.
        const cases = await loadCases(sessionScope);
        if (cases === null || !isSessionScopeCurrent(sessionScope)) {
          return supersededFailure();
        }
        // Load the new matter through the normal path so its state, epoch and
        // membership are established exactly as any other matter's would be —
        // rather than synthesising a half-populated case locally.
        dispatch({ type: "boot_loading" });
        return await loadCase(created.caseFile.id, sessionScope);
      } catch (error) {
        return {
          ok: false,
          error:
            error instanceof api.ApiError
              ? error.message
              : "Could not open the matter.",
        };
      }
    },
    [
      captureSessionScope,
      isSessionScopeCurrent,
      loadCase,
      loadCases,
      supersededFailure,
    ],
  );

  const logout = useCallback(async () => {
    if (logoutInFlightRef.current) return;
    logoutInFlightRef.current = true;
    // Invalidate work synchronously; the server-side revocation can finish in
    // without giving old responses a chance to land locally. Keep the login
    // form unavailable until the cookie-clearing response has settled so a
    // late logout response cannot erase a newly issued session cookie.
    clearServerSession();
    dispatch({ type: "boot_loading" });
    try {
      await api.logout();
    } catch {
      // Whatever the server says, drop the local session.
    } finally {
      logoutInFlightRef.current = false;
    }
    if (sessionReviewerIdRef.current === null) {
      dispatch({ type: "session_cleared" });
    }
  }, [clearServerSession]);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string): Promise<SimpleOutcome> => {
      const wasForced = stateRef.current.passwordGate === "forced";
      const sessionScope = captureSessionScope();
      if (sessionScope === null) return supersededFailure();
      try {
        await api.changePassword({ currentPassword, newPassword });
        if (!isSessionScopeCurrent(sessionScope)) return supersededFailure();
        dispatch({ type: "password_changed" });
        if (wasForced) {
          dispatch({ type: "boot_loading" });
          try {
            const cases = await loadCases(sessionScope);
            if (cases === null) return supersededFailure();
            return await loadCase(cases[0]?.caseFile.id, sessionScope);
          } catch (error) {
            if (!isSessionScopeCurrent(sessionScope)) return supersededFailure();
            if (api.isUnauthorized(error)) {
              clearServerSession();
              return { ok: false, error: "Your session has expired — sign in again." };
            }
            dispatch({ type: "boot_failed", error: api.errorMessage(error) });
            return { ok: false, error: api.errorMessage(error) };
          }
        }
        return { ok: true };
      } catch (error) {
        if (!isSessionScopeCurrent(sessionScope)) return supersededFailure();
        if (api.isUnauthorized(error) && api.errorMessage(error) === "Not signed in.") {
          clearServerSession();
        }
        return { ok: false, error: api.errorMessage(error) };
      }
    },
    [
      captureSessionScope,
      clearServerSession,
      isSessionScopeCurrent,
      loadCase,
      loadCases,
      supersededFailure,
    ],
  );

  /** Map a server failure only when it still belongs to the active operation. */
  const serverFailure = useCallback(
    (error: unknown, scope: OperationScope): { ok: false; error: string } => {
      if (!isOperationScopeCurrent(scope)) return supersededFailure();
      if (api.isUnauthorized(error)) {
        clearServerSession();
        return { ok: false, error: "Your session has expired — sign in again." };
      }
      return { ok: false, error: api.errorMessage(error) };
    },
    [clearServerSession, isOperationScopeCurrent, supersededFailure],
  );

  const staleWriteFailure = useCallback(
    async (
      error: unknown,
      scope: CaseOperationScope,
      preserveDraft: boolean,
    ): Promise<{ ok: false; error: string }> => {
      if (!isCaseScopeCurrent(scope)) return supersededFailure();
      if (!api.isConflict(error)) return serverFailure(error, scope);
      const originalMessage = api.errorMessage(error);
      try {
        const payload = await api.fetchCaseById(scope.caseId);
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        if (payload.caseFile.id !== scope.caseId) {
          return { ok: false, error: "The case service returned the wrong matter." };
        }
        dispatch({ type: "case_refreshed", payload });
        return {
          ok: false,
          error: `${originalMessage} The latest matter state has been loaded. Review it before trying again.${
            preserveDraft ? " Your unsaved input remains in this form." : ""
          }`,
        };
      } catch (refreshError) {
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        if (api.isUnauthorized(refreshError)) return serverFailure(refreshError, scope);
        return {
          ok: false,
          error: `${originalMessage} The latest matter state could not be loaded: ${api.errorMessage(
            refreshError,
          )}`,
        };
      }
    },
    [isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const startMfaEnrollment = useCallback(async () => {
    const scope = captureSessionScope();
    if (scope === null) return supersededFailure();
    try {
      const enrollment = await api.postMfaEnroll();
      if (!isSessionScopeCurrent(scope)) return supersededFailure();
      return { ok: true as const, enrollment };
    } catch (error) {
      return serverFailure(error, scope);
    }
  }, [captureSessionScope, isSessionScopeCurrent, serverFailure, supersededFailure]);

  const enableMfa = useCallback(
    async (code: string): Promise<SimpleOutcome> => {
      const scope = captureSessionScope();
      if (scope === null) return supersededFailure();
      try {
        const status = await api.postMfaEnable({ code });
        if (!isSessionScopeCurrent(scope)) return supersededFailure();
        dispatch({ type: "mfa_status", status });
        return { ok: true };
      } catch (error) {
        if (!isSessionScopeCurrent(scope)) return supersededFailure();
        if (api.isInvalidMfaCode(error)) {
          return { ok: false, error: api.errorMessage(error) };
        }
        return serverFailure(error, scope);
      }
    },
    [
      captureSessionScope,
      isSessionScopeCurrent,
      serverFailure,
      supersededFailure,
    ],
  );

  const verifyMfa = useCallback(
    async (code: string): Promise<SimpleOutcome> => {
      const scope = captureSessionScope();
      if (scope === null) return supersededFailure();
      try {
        const status = await api.postMfaVerify({ code });
        if (!isSessionScopeCurrent(scope)) return supersededFailure();
        dispatch({ type: "mfa_status", status });
        return { ok: true };
      } catch (error) {
        if (!isSessionScopeCurrent(scope)) return supersededFailure();
        if (api.isInvalidMfaCode(error)) {
          return { ok: false, error: api.errorMessage(error) };
        }
        return serverFailure(error, scope);
      }
    },
    [
      captureSessionScope,
      isSessionScopeCurrent,
      serverFailure,
      supersededFailure,
    ],
  );

  // Persist the mutable case record (never ephemeral UI state) on change —
  // local mode only; in server mode the backend is the record.
  useEffect(() => {
    if (state.dataMode === "server") return;
    saveSnapshot(toSnapshot(state));
  }, [
    state.dataMode,
    state.claims,
    state.conflicts,
    state.conflictOrder,
    state.decisions,
    state.auditEvents,
    state.reportSections,
    state.activeReviewerId,
  ]);

  const saveDecision = useCallback(
    async (
      partial: Omit<DecisionInput, "reviewerName" | "reviewerRole">,
    ): Promise<SaveDecisionOutcome> => {
      const s = stateRef.current;
      const reviewer = s.reviewers.find((r) => r.id === s.activeReviewerId);
      if (!reviewer) {
        return { ok: false, error: "No active reviewer selected." };
      }
      if (s.dataMode === "server") {
        const scope = captureCaseScope(s.caseFile.id);
        if (scope === null) return supersededFailure();
        const conflict = s.conflicts.get(partial.conflictId);
        if (!conflict) {
          return { ok: false, error: "Conflict not found." };
        }
        if (typeof conflict.version !== "number") {
          return {
            ok: false,
            error: "Conflict version is unavailable. Reload the matter before saving.",
          };
        }
        try {
          const result = await api.postCaseDecision(s.caseFile.id, partial.conflictId, {
            decisionType: partial.decisionType,
            selectedClaimId: partial.selectedClaimId,
            reasoning: partial.reasoning,
            expectedVersion: conflict.version,
          });
          if (!isCaseScopeCurrent(scope)) return supersededFailure();
          dispatch({ type: "decision_applied", ...result });
          return { ok: true, decision: result.decision };
        } catch (error) {
          return await staleWriteFailure(error, scope, true);
        }
      }
      const input: DecisionInput = {
        ...partial,
        reviewerName: reviewer.name,
        reviewerRole: reviewer.role,
      };
      const validationError = validateDecision(input);
      if (validationError) {
        return { ok: false, error: validationError };
      }
      const conflict = s.conflicts.get(input.conflictId);
      if (!conflict) {
        return { ok: false, error: "Conflict not found." };
      }
      const claimA = s.claims.get(conflict.claimAId);
      const claimB = s.claims.get(conflict.claimBId);
      if (!claimA || !claimB) {
        return { ok: false, error: "Conflict claims not found." };
      }
      const result = applyDecision(
        {
          conflict,
          claimA,
          claimB,
          conflictsById: s.conflicts,
          now: new Date().toISOString(),
          makeId: () => crypto.randomUUID(),
        },
        input,
      );
      dispatch({ type: "decision_applied", ...result });
      return { ok: true, decision: result.decision };
    },
    [captureCaseScope, isCaseScopeCurrent, staleWriteFailure, supersededFailure],
  );

  const createClaimAction = useCallback(
    async (input: NewClaimInput): Promise<CreateClaimOutcome> => {
      const s = stateRef.current;
      const reviewer = s.reviewers.find((r) => r.id === s.activeReviewerId);
      if (!reviewer) {
        return { ok: false, error: "No active reviewer selected." };
      }
      if (s.dataMode === "server") {
        const scope = captureCaseScope(s.caseFile.id);
        if (scope === null) return supersededFailure();
        try {
          const result = await api.postCaseClaim(s.caseFile.id, {
            sourceDocumentId: input.sourceDocumentId,
            text: input.text,
            pageRef: input.pageRef,
            quote: input.quote,
            confidence: input.confidence,
            evidenceQuality: input.evidenceQuality,
            sourcePageExtractionId: input.sourcePageExtractionId ?? null,
          });
          if (!isCaseScopeCurrent(scope)) return supersededFailure();
          dispatch({ type: "claim_created", ...result });
          return { ok: true, claim: result.claim };
        } catch (error) {
          return serverFailure(error, scope);
        }
      }
      const validationError = validateNewClaim(input);
      if (validationError) {
        return { ok: false, error: validationError };
      }
      const source = s.sources.get(input.sourceDocumentId);
      if (!source) {
        return { ok: false, error: "Source document not found." };
      }
      const result = createClaim(
        {
          source,
          claimId: `clm-${s.claims.size + 1}`,
          eventId: crypto.randomUUID(),
          now: new Date().toISOString(),
          actorName: reviewer.name,
          actorRole: reviewer.role,
        },
        input,
      );
      dispatch({ type: "claim_created", ...result });
      return { ok: true, claim: result.claim };
    },
    [
      captureCaseScope,
      isCaseScopeCurrent,
      serverFailure,
      supersededFailure,
    ],
  );

  const flagConflictAction = useCallback(
    async (input: FlagConflictInput): Promise<FlagConflictOutcome> => {
      const s = stateRef.current;
      const reviewer = s.reviewers.find((r) => r.id === s.activeReviewerId);
      if (!reviewer) {
        return { ok: false, error: "No active reviewer selected." };
      }
      if (s.dataMode === "server") {
        const scope = captureCaseScope(s.caseFile.id);
        if (scope === null) return supersededFailure();
        try {
          const result = await api.postCaseConflict(s.caseFile.id, {
            claimAId: input.claimAId,
            claimBId: input.claimBId,
            conflictType: input.conflictType,
            severity: input.severity,
            summary: input.summary,
          });
          if (!isCaseScopeCurrent(scope)) return supersededFailure();
          dispatch({ type: "conflict_flagged", ...result });
          return { ok: true, conflict: result.conflict };
        } catch (error) {
          return serverFailure(error, scope);
        }
      }
      const validationError = validateFlagConflict(input, s.claims, s.conflicts);
      if (validationError) {
        return { ok: false, error: validationError };
      }
      const result = flagConflict(
        {
          claimsById: s.claims,
          conflictsById: s.conflicts,
          conflictId: `cf-${s.conflicts.size + 1}`,
          now: new Date().toISOString(),
          makeEventId: () => crypto.randomUUID(),
          actorName: reviewer.name,
          actorRole: reviewer.role,
        },
        input,
      );
      dispatch({ type: "conflict_flagged", ...result });
      return { ok: true, conflict: result.conflict };
    },
    [
      captureCaseScope,
      isCaseScopeCurrent,
      serverFailure,
      supersededFailure,
    ],
  );

  const saveReportSectionAction = useCallback(
    async (input: ReportSectionInput, sectionId?: string): Promise<SaveSectionOutcome> => {
      const s = stateRef.current;
      const reviewer = s.reviewers.find((r) => r.id === s.activeReviewerId);
      if (!reviewer) {
        return { ok: false, error: "No active reviewer selected." };
      }
      if (s.dataMode === "server") {
        const scope = captureCaseScope(s.caseFile.id);
        if (scope === null) return supersededFailure();
        try {
          const body = {
            title: input.title,
            paragraphRef: input.paragraphRef,
            text: input.text,
            claimIds: input.claimIds,
          };
          let result: api.SectionResponse;
          if (sectionId) {
            const existing = s.reportSections.find((section) => section.id === sectionId);
            if (!existing) {
              return { ok: false, error: "Report section not found." };
            }
            if (typeof existing.version !== "number") {
              return {
                ok: false,
                error: "Report section version is unavailable. Reload the matter before saving.",
              };
            }
            result = await api.putCaseSection(s.caseFile.id, sectionId, {
              ...body,
              expectedVersion: existing.version,
            });
          } else {
            result = await api.postCaseSection(s.caseFile.id, body);
          }
          if (!isCaseScopeCurrent(scope)) return supersededFailure();
          dispatch({ type: "section_saved", ...result });
          return { ok: true, section: result.section };
        } catch (error) {
          return sectionId
            ? await staleWriteFailure(error, scope, true)
            : serverFailure(error, scope);
        }
      }
      const validationError = validateReportSection(
        input,
        s.claims,
        s.reportSections,
        sectionId ?? null,
      );
      if (validationError) {
        return { ok: false, error: validationError };
      }
      const newImpact = computeSectionImpact(
        { claimIds: input.claimIds, paragraphRef: input.paragraphRef.trim() },
        s.claims,
        s.conflicts,
      ).status;
      const base = {
        now: new Date().toISOString(),
        eventId: crypto.randomUUID(),
        actorName: reviewer.name,
        actorRole: reviewer.role,
        newImpact,
      };
      let result;
      if (sectionId) {
        const existing = s.reportSections.find((sec) => sec.id === sectionId);
        if (!existing) {
          return { ok: false, error: "Report section not found." };
        }
        result = updateReportSection(
          {
            ...base,
            existing,
            previousImpact: computeSectionImpact(existing, s.claims, s.conflicts).status,
          },
          input,
        );
      } else {
        result = createReportSection(
          {
            ...base,
            previousImpact: null,
            sectionId: `rpt-${s.reportSections.length + 1}`,
            caseId: s.caseFile.id,
          },
          input,
        );
      }
      dispatch({ type: "section_saved", ...result });
      return { ok: true, section: result.section };
    },
    [
      captureCaseScope,
      isCaseScopeCurrent,
      serverFailure,
      staleWriteFailure,
      supersededFailure,
    ],
  );

  /** Server mode: mark the section's current text as approved for the
   *  production packet. Local mode has no revision/approval concept —
   *  every eligible section is already includable there. */
  const approveReportSectionAction = useCallback(
    async (sectionId: string): Promise<SaveSectionOutcome> => {
      const s = stateRef.current;
      if (s.dataMode !== "server") {
        return { ok: false, error: "Approval is a server-mode capability." };
      }
      const scope = captureCaseScope(s.caseFile.id);
      if (scope === null) return supersededFailure();
      const section = s.reportSections.find((candidate) => candidate.id === sectionId);
      if (!section) {
        return { ok: false, error: "Report section not found." };
      }
      if (typeof section.version !== "number") {
        return {
          ok: false,
          error: "Report section version is unavailable. Reload the matter before approving.",
        };
      }
      try {
        const result = await api.approveCaseSection(
          s.caseFile.id,
          sectionId,
          section.version,
        );
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        dispatch({ type: "section_saved", ...result });
        return { ok: true, section: result.section };
      } catch (error) {
        return await staleWriteFailure(error, scope, false);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, staleWriteFailure, supersededFailure],
  );

  const recordPacketExport = useCallback(
    async (record: PacketExportRecord): Promise<SimpleOutcome> => {
      const s = stateRef.current;
      const reviewer = s.reviewers.find((r) => r.id === s.activeReviewerId);
      if (!reviewer) {
        return { ok: false, error: "No active reviewer selected." };
      }
      if (s.dataMode === "server") {
        // Server mode generates packets server-side (generateServerPacket);
        // client-side packet assembly is a local-mode capability only.
        return { ok: false, error: "Packets are generated server-side in this mode." };
      }
      dispatch({
        type: "audit_appended",
        event: {
          id: crypto.randomUUID(),
          caseId: s.caseFile.id,
          at: new Date().toISOString(),
          actor: reviewer.name,
          actorRole: reviewer.role,
          action: "evidence packet generated",
          subjectType: "export",
          subjectId: record.packetId,
          previousStatus: null,
          newStatus: null,
          detail: `${record.packetType} packet: ${record.included} section(s) included, ${record.excluded} excluded, ${record.withheld} withheld under privilege. Body SHA-256 ${record.sha256}.`,
          reportEligibilityChanged: false,
        },
      });
      return { ok: true };
    },
    [],
  );

  const generateServerPacket = useCallback(
    async (
      packetType: string,
    ): Promise<{ ok: true; packet: api.ServerPacket } | { ok: false; error: string }> => {
      const s = stateRef.current;
      const scope = captureCaseScope(s.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const packet = await api.generateCasePacket(s.caseFile.id, { packetType });
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        dispatch({ type: "audit_appended", event: packet.auditEvent });
        return { ok: true, packet };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const fetchPacketArtifacts = useCallback(
    async (input: { limit?: number; offset?: number } = {}): Promise<PacketArtifactListOutcome> => {
      const s = stateRef.current;
      const scope = captureCaseScope(s.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.fetchCasePackets(s.caseFile.id, input);
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        return { ok: true, result };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const fetchPacketArtifact = useCallback(
    async (packetId: string): Promise<PacketArtifactDetailOutcome> => {
      const s = stateRef.current;
      const scope = captureCaseScope(s.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const packet = await api.fetchCasePacket(s.caseFile.id, packetId);
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        return { ok: true, packet };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const fetchAdminReviewers = useCallback(async (): Promise<AdminReviewersOutcome> => {
    const scope = captureSessionScope();
    if (scope === null) return supersededFailure();
    try {
      const result = await api.fetchAdminReviewers();
      if (!isSessionScopeCurrent(scope)) return supersededFailure();
      return { ok: true, reviewers: result.reviewers };
    } catch (error) {
      return serverFailure(error, scope);
    }
  }, [captureSessionScope, isSessionScopeCurrent, serverFailure, supersededFailure]);

  const applyAdminReviewerResult = useCallback(
    (
      result: api.AdminReviewerResponse,
      scope: CaseOperationScope,
    ): AdminReviewerOutcome => {
      if (!isCaseScopeCurrent(scope)) return supersededFailure();
      dispatch({
        type: "reviewer_admin_changed",
        reviewer: result.reviewer,
      });
      return {
        ok: true,
        reviewer: result.reviewer,
        accountAuditEvent: result.accountAuditEvent,
      };
    },
    [isCaseScopeCurrent, supersededFailure],
  );

  const fetchAdminAccountAudit = useCallback(async () => {
    const scope = captureSessionScope();
    if (scope === null) return supersededFailure();
    try {
      const result = await api.fetchAdminAccountAudit();
      if (!isSessionScopeCurrent(scope)) return supersededFailure();
      return { ok: true as const, events: result.events };
    } catch (error) {
      return serverFailure(error, scope);
    }
  }, [captureSessionScope, isSessionScopeCurrent, serverFailure, supersededFailure]);

  const createAdminReviewer = useCallback(
    async (input: {
      name: string;
      username: string;
      role: string;
      initialPassword: string;
    }): Promise<AdminReviewerOutcome> => {
      const scope = captureCaseScope(stateRef.current.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postAdminReviewer(input);
        return applyAdminReviewerResult(result, scope);
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [
      applyAdminReviewerResult,
      captureCaseScope,
      serverFailure,
      supersededFailure,
    ],
  );

  const setAdminReviewerActive = useCallback(
    async (reviewerId: string, isActive: boolean): Promise<AdminReviewerOutcome> => {
      const scope = captureCaseScope(stateRef.current.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postAdminReviewerActive(reviewerId, isActive);
        return applyAdminReviewerResult(result, scope);
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [
      applyAdminReviewerResult,
      captureCaseScope,
      serverFailure,
      supersededFailure,
    ],
  );

  const resetAdminReviewerPassword = useCallback(
    async (reviewerId: string, newPassword: string): Promise<AdminReviewerOutcome> => {
      const scope = captureCaseScope(stateRef.current.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postAdminReviewerPassword(reviewerId, {
          newPassword,
        });
        return applyAdminReviewerResult(result, scope);
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [
      applyAdminReviewerResult,
      captureCaseScope,
      serverFailure,
      supersededFailure,
    ],
  );

  const resetAdminReviewerMfa = useCallback(
    async (reviewerId: string): Promise<AdminReviewerOutcome> => {
      const scope = captureCaseScope(stateRef.current.caseFile.id);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postAdminReviewerMfaReset(reviewerId);
        return applyAdminReviewerResult(result, scope);
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [
      applyAdminReviewerResult,
      captureCaseScope,
      serverFailure,
      supersededFailure,
    ],
  );

  const fetchCaseMembers = useCallback(
    async (caseId: string): Promise<AdminCaseMembersOutcome> => {
      const scope = captureCaseScope(caseId);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.fetchCaseMembers(caseId);
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        return { ok: true, members: result.members };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const addCaseMember = useCallback(
    async (input: {
      caseId: string;
      reviewerId: string;
      role: string;
    }): Promise<AdminCaseMemberOutcome> => {
      const scope = captureCaseScope(input.caseId);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postCaseMember(input.caseId, {
          reviewerId: input.reviewerId,
          role: input.role,
        });
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        return {
          ok: true,
          member: result.member,
          accountAuditEvent: result.accountAuditEvent,
        };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const setCaseMemberActive = useCallback(
    async (
      caseId: string,
      reviewerId: string,
      isActive: boolean,
    ): Promise<AdminCaseMemberOutcome> => {
      const scope = captureCaseScope(caseId);
      if (scope === null) return supersededFailure();
      try {
        const result = await api.postCaseMemberActive(caseId, reviewerId, isActive);
        if (!isCaseScopeCurrent(scope)) return supersededFailure();
        return {
          ok: true,
          member: result.member,
          accountAuditEvent: result.accountAuditEvent,
        };
      } catch (error) {
        return serverFailure(error, scope);
      }
    },
    [captureCaseScope, isCaseScopeCurrent, serverFailure, supersededFailure],
  );

  const resetCase = useCallback(async () => {
    if (stateRef.current.dataMode === "server") return;
    clearSnapshot();
    dispatch({ type: "case_reset" });
  }, []);

  const value = useMemo<StoreValue>(
    () => ({
      state,
      selectView: (view) => dispatch({ type: "select_view", view }),
      selectConflict: (conflictId) => dispatch({ type: "select_conflict", conflictId }),
      selectClaim: (claimId) => dispatch({ type: "select_claim", claimId }),
      setReviewer: (reviewerId) => dispatch({ type: "set_reviewer", reviewerId }),
      retryBoot: () => void bootFromServer(),
      login,
      switchCase,
      createMatter,
      logout,
      changePassword,
      requestPasswordChange: () => dispatch({ type: "password_gate", gate: "voluntary" }),
      dismissPasswordChange: () => dispatch({ type: "password_gate", gate: null }),
      requestMfa: () => dispatch({ type: "mfa_gate", open: true }),
      dismissMfa: () => dispatch({ type: "mfa_gate", open: false }),
      startMfaEnrollment,
      enableMfa,
      verifyMfa,
      saveDecision,
      createClaim: createClaimAction,
      flagConflict: flagConflictAction,
      saveReportSection: saveReportSectionAction,
      approveReportSection: approveReportSectionAction,
      recordPacketExport,
      generateServerPacket,
      fetchPacketArtifacts,
      fetchPacketArtifact,
      fetchAdminReviewers,
      fetchAdminAccountAudit,
      createAdminReviewer,
      setAdminReviewerActive,
      resetAdminReviewerPassword,
      resetAdminReviewerMfa,
      fetchCaseMembers,
      addCaseMember,
      setCaseMemberActive,
      resetCase,
    }),
    [
      state,
      bootFromServer,
      login,
      switchCase,
      createMatter,
      logout,
      changePassword,
      startMfaEnrollment,
      enableMfa,
      verifyMfa,
      saveDecision,
      createClaimAction,
      flagConflictAction,
      saveReportSectionAction,
      approveReportSectionAction,
      recordPacketExport,
      generateServerPacket,
      fetchPacketArtifacts,
      fetchPacketArtifact,
      fetchAdminReviewers,
      fetchAdminAccountAudit,
      createAdminReviewer,
      setAdminReviewerActive,
      resetAdminReviewerPassword,
      resetAdminReviewerMfa,
      fetchCaseMembers,
      addCaseMember,
      setCaseMemberActive,
      resetCase,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreValue {
  const value = useContext(StoreContext);
  if (!value) {
    throw new Error("useStore must be used within StoreProvider");
  }
  return value;
}

/** Role governing actions inside the currently loaded matter.
 *  Account role remains separate and is used for account administration. */
export function effectiveMatterRole(state: AppState): ReviewerRole | null {
  if (state.dataMode === "server") {
    return state.caseMembership?.role ?? null;
  }
  return (
    state.reviewers.find((reviewer) => reviewer.id === state.activeReviewerId)?.role ?? null
  );
}

/** The claim currently focused in the Evidence Rail. */
export function focusedClaim(state: AppState): Claim | null {
  if (state.selectedClaimId) {
    return state.claims.get(state.selectedClaimId) ?? null;
  }
  const conflict = state.conflicts.get(state.selectedConflictId);
  if (!conflict) return null;
  return state.claims.get(conflict.claimAId) ?? null;
}
