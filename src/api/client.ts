/**
 * Typed client for the Atlas Argus backend (see docs/api-contract.md).
 *
 * The wire format matches `src/domain/types.ts` field-for-field, so responses
 * are the domain types directly. Error responses carry `{detail}` with the
 * same wording as the frontend's own domain validation.
 *
 * `VITE_API_URL` selects the mode: set → server-authoritative, unset → the
 * store runs fully local (frontend state + localStorage).
 */
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
  SourcePage,
} from "../domain/types";

export interface AdminReviewer extends Reviewer {
  username: string;
  isActive: boolean;
  mustChangePassword: boolean;
  passwordChangedAt: string | null;
  mfaEnabled: boolean;
}

export interface AccountAuditEvent {
  id: string;
  at: string;
  actorReviewerId: string;
  actor: string;
  actorRole: string;
  action: string;
  subjectReviewerId: string;
  previousStatus: string | null;
  newStatus: string | null;
  detail: string;
}

export interface CaseMembership {
  caseId: string;
  reviewerId: string;
  role: ReviewerRole;
  isActive: boolean;
}

export interface CaseSummary {
  caseFile: CaseFile;
  caseMembership: CaseMembership;
}

export interface CasePayload {
  caseFile: CaseFile;
  caseMembership: CaseMembership;
  reviewers: Reviewer[];
  sources: SourceDocument[];
  claims: Claim[];
  conflicts: Conflict[];
  conflictOrder: string[];
  decisions: ReviewDecision[];
  auditEvents: AuditEvent[];
  reportSections: ReportSection[];
}

export interface DecisionResponse {
  conflict: Conflict;
  claims: Claim[];
  decision: ReviewDecision;
  auditEvents: AuditEvent[];
}

export interface ClaimResponse {
  claim: Claim;
  auditEvent: AuditEvent;
}

export interface ConflictResponse {
  conflict: Conflict;
  claims: Claim[];
  auditEvents: AuditEvent[];
}

export interface SectionResponse {
  section: ReportSection;
  auditEvent: AuditEvent;
}

export interface AdminReviewerResponse {
  reviewer: AdminReviewer;
  accountAuditEvent: AccountAuditEvent;
}

export interface AdminCaseMember {
  caseId: string;
  reviewerId: string;
  reviewerName: string;
  username: string;
  reviewerRole: ReviewerRole;
  role: ReviewerRole;
  isActive: boolean;
  reviewerIsActive: boolean;
}

export interface AdminCaseMemberResponse {
  member: AdminCaseMember;
  accountAuditEvent: AccountAuditEvent;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const REQUEST_TIMEOUT_MS = 20_000;

/** `VITE_API_URL=/` means same-origin (the backend serves the built
 *  frontend) and yields an empty base — requests go to relative `/api/…`. */
function baseUrl(): string | null {
  const url = import.meta.env.VITE_API_URL;
  if (!url) return null;
  return url.replace(/\/+$/, "");
}

export function apiEnabled(): boolean {
  return baseUrl() !== null;
}

/** Document ingestion runs OCR synchronously and legitimately takes minutes on
 *  a long scanned exhibit. The default timeout is sized for ordinary JSON
 *  calls, so aborting an upload at that mark would look like a server fault
 *  and — worse — leave the user retrying work that was still succeeding. */
// The server permits at most 15 minutes of ingestion. Leave one minute for
// request transfer and response delivery so the browser never gives up first.
export const UPLOAD_TIMEOUT_MS = 16 * 60 * 1000;
export const PACKET_GENERATION_TIMEOUT_MS = 2 * 60 * 1000;
export const PDF_DOWNLOAD_TIMEOUT_MS = 2 * 60 * 1000;

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const base = baseUrl();
  if (base === null) {
    throw new ApiError("API is not configured (VITE_API_URL unset).", null);
  }
  const controller = new AbortController();
  const upstreamSignal = init?.signal;
  const abortFromUpstream = () => controller.abort(upstreamSignal?.reason);
  if (upstreamSignal?.aborted) {
    abortFromUpstream();
  } else {
    upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  }
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetch(`${base}/api${path}`, {
      headers: { "content-type": "application/json" },
      credentials: "include",
      ...init,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `Case service error (${response.status}).`;
      try {
        const body: unknown = await response.json();
        if (
          typeof body === "object" &&
          body !== null &&
          typeof (body as { detail?: unknown }).detail === "string"
        ) {
          detail = (body as { detail: string }).detail;
        }
      } catch {
        // keep the generic message
      }
      throw new ApiError(detail, response.status);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) {
      throw new ApiError("Case service request timed out — try again.", null);
    }
    if (upstreamSignal?.aborted) {
      throw new ApiError("Request cancelled.", null);
    }
    throw new ApiError(
      "Case service unreachable — check that the server is running.",
      null,
    );
  } finally {
    globalThis.clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

function post<T>(
  path: string,
  body: unknown,
  timeoutMs?: number,
  init?: RequestInit,
): Promise<T> {
  return request<T>(
    path,
    { ...init, method: "POST", body: JSON.stringify(body) },
    timeoutMs,
  );
}

export interface SessionInfo {
  reviewer: Reviewer;
  mustChangePassword: boolean;
  mfa: MfaStatus;
  /** Absolute ISO-8601 expiry for the current server session. */
  expiresAt: string;
}

export interface MfaStatus {
  enabled: boolean;
  verified: boolean;
}

export interface MfaEnrollment {
  secret: string;
  otpauthUri: string;
}

export const login = (body: {
  username: string;
  password: string;
}): Promise<SessionInfo> => post("/auth/login", body);

export const logout = (): Promise<{ status: string }> => post("/auth/logout", {});

export const fetchSession = (): Promise<SessionInfo> => request("/auth/session");

export const fetchMfaStatus = (): Promise<MfaStatus> => request("/auth/mfa/status");

export const postMfaEnroll = (): Promise<MfaEnrollment> => post("/auth/mfa/enroll", {});

export const postMfaEnable = (body: { code: string }): Promise<MfaStatus> =>
  post("/auth/mfa/enable", body);

export const postMfaVerify = (body: { code: string }): Promise<MfaStatus> =>
  post("/auth/mfa/verify", body);

export const changePassword = (body: {
  currentPassword: string;
  newPassword: string;
}): Promise<{ status: string }> => post("/auth/change-password", body);

export const fetchCase = (): Promise<CasePayload> => request("/case");

const casePath = (caseId: string, suffix = "") =>
  `/cases/${encodeURIComponent(caseId)}${suffix}`;

export const fetchCases = (): Promise<{ cases: CaseSummary[] }> => request("/cases");

export const fetchCaseById = (caseId: string): Promise<CasePayload> =>
  request(casePath(caseId));

export const postDecision = (
  conflictId: string,
  body: {
    decisionType: string;
    selectedClaimId: string | null;
    reasoning: string;
    expectedVersion: number;
  },
): Promise<DecisionResponse> => post(`/conflicts/${conflictId}/decisions`, body);

export const postCaseDecision = (
  caseId: string,
  conflictId: string,
  body: {
    decisionType: string;
    selectedClaimId: string | null;
    reasoning: string;
    expectedVersion: number;
  },
): Promise<DecisionResponse> =>
  post(casePath(caseId, `/conflicts/${encodeURIComponent(conflictId)}/decisions`), body);

export const postClaim = (body: {
  sourceDocumentId: string;
  text: string;
  pageRef: string;
  quote: string;
  confidence: number;
  evidenceQuality: string;
}): Promise<ClaimResponse> => post("/claims", body);

export const postCaseClaim = (
  caseId: string,
  body: {
    sourceDocumentId: string;
    text: string;
    pageRef: string;
    quote: string;
    confidence: number;
    evidenceQuality: string;
    /** Cite an extracted page and the server checks the quote against it.
     *  There is no field for the verification result: that is the server's
     *  conclusion, and a client-supplied value is ignored. */
    sourcePageExtractionId?: string | null;
  },
): Promise<ClaimResponse> => post(casePath(caseId, "/claims"), body);

/** Fetch the server-rendered packet PDF.
 *
 *  Deliberately not a plain link: the endpoint requires the session cookie and
 *  an MFA-verified session, and it refuses to serve an artifact that fails its
 *  integrity check — so the response has to be inspected rather than handed to
 *  the browser's downloader, which would silently save an error page as a
 *  ".pdf".
 */
export async function fetchPacketPdf(
  caseId: string,
  packetId: string,
  expectedSha256: string,
): Promise<{ blob: Blob; filename: string }> {
  const base = baseUrl();
  if (base === null) {
    throw new ApiError("API is not configured (VITE_API_URL unset).", null);
  }
  const controller = new AbortController();
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, PDF_DOWNLOAD_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${base}/api${casePath(caseId, `/packets/${encodeURIComponent(packetId)}/pdf`)}`,
      { credentials: "include", signal: controller.signal },
    );
    if (!response.ok) {
      let detail = `Could not download the packet PDF (${response.status}).`;
      try {
        const body: unknown = await response.json();
        if (
          typeof body === "object" &&
          body !== null &&
          typeof (body as { detail?: unknown }).detail === "string"
        ) {
          detail = (body as { detail: string }).detail;
        }
      } catch {
        // keep the generic message
      }
      throw new ApiError(detail, response.status);
    }
    if (!(response.headers.get("content-type") ?? "").startsWith("application/pdf")) {
      throw new ApiError("The packet service returned a non-PDF response.", null);
    }
    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    const bytes = await response.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const actualSha256 = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    if (actualSha256 !== expectedSha256) {
      throw new ApiError(
        "Downloaded PDF failed its SHA-256 integrity check.",
        null,
      );
    }
    return {
      blob: new Blob([bytes], { type: "application/pdf" }),
      filename: match?.[1] ?? `${packetId}.pdf`,
    };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) {
      throw new ApiError("Packet PDF download timed out — try again.", null);
    }
    throw new ApiError("Could not download the packet PDF.", null);
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export interface CreateMatterResponse {
  caseFile: CaseFile;
  caseMembership: { caseId: string; reviewerId: string; role: ReviewerRole; isActive: boolean };
  auditEvent?: AuditEvent;
}

/** Open a new matter. The id is generated server-side and cannot be supplied —
 *  see migration 0018 for why that property is the security boundary. */
export const postCase = (body: {
  name: string;
  aircraft: string;
  accidentDate: string;
  location: string;
  matterType: string;
  docketRef: string;
}): Promise<CreateMatterResponse> => post("/cases", body);

export interface UploadSourceResponse {
  source: SourceDocument;
  /** Byte-identical sources already in this matter, filtered to those the
   *  caller may see. Informational: identical bytes are a legitimate second
   *  evidentiary record, so this never blocks the upload. */
  possibleDuplicateSourceIds: string[];
  auditEvent?: AuditEvent;
  /** True when this request was a retry of one that already succeeded. */
  replayed: boolean;
}

export const postCaseSource = (
  caseId: string,
  body: {
    title: string;
    type: string;
    origin: string;
    custodian: string;
    docketRef: string | null;
    privilegeStatus: string;
    originalFilename: string;
    contentBase64: string;
    idempotencyKey?: string;
  },
  options: { signal?: AbortSignal } = {},
): Promise<UploadSourceResponse> =>
  // Extraction is synchronous and OCR-bound, so this one call gets the long
  // timeout; every other call keeps the short default.
  post(casePath(caseId, "/sources"), body, UPLOAD_TIMEOUT_MS, options);

export interface SourcePagesResponse {
  sourceId: string;
  total: number;
  offset: number;
  limit: number;
  pages: SourcePage[];
}

export const getCaseSourcePages = (
  caseId: string,
  sourceId: string,
  options: { offset?: number; limit?: number } = {},
): Promise<SourcePagesResponse> => {
  const params = new URLSearchParams();
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const query = params.toString();
  return request(
    casePath(
      caseId,
      `/sources/${encodeURIComponent(sourceId)}/pages${query ? `?${query}` : ""}`,
    ),
  );
};

/** Load the exact uploaded source PDF for in-context review.
 *
 * The server verifies its stored bytes before responding; the browser verifies
 * the response again against the hash already present in the matter payload so
 * a stale or altered response is never displayed as evidence.
 */
export async function fetchSourcePdf(
  caseId: string,
  sourceId: string,
  expectedSha256: string,
): Promise<Blob> {
  const base = baseUrl();
  if (base === null) {
    throw new ApiError("API is not configured (VITE_API_URL unset).", null);
  }
  const controller = new AbortController();
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, PDF_DOWNLOAD_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${base}/api${casePath(
        caseId,
        `/sources/${encodeURIComponent(sourceId)}/file`,
      )}`,
      { credentials: "include", signal: controller.signal },
    );
    if (!response.ok) {
      let detail = `Could not open the source document (${response.status}).`;
      try {
        const body: unknown = await response.json();
        if (
          typeof body === "object" &&
          body !== null &&
          typeof (body as { detail?: unknown }).detail === "string"
        ) {
          detail = (body as { detail: string }).detail;
        }
      } catch {
        // Keep the response-status message when the body is not JSON.
      }
      throw new ApiError(detail, response.status);
    }
    if (!(response.headers.get("content-type") ?? "").startsWith("application/pdf")) {
      throw new ApiError("The source service returned a non-PDF response.", null);
    }
    const bytes = await response.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const actualSha256 = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    const headerSha256 = response.headers.get("x-content-sha256");
    if (actualSha256 !== expectedSha256 || headerSha256 !== expectedSha256) {
      throw new ApiError(
        "The source response does not match the evidence hash and cannot be displayed.",
        null,
      );
    }
    return new Blob([bytes], { type: "application/pdf" });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) {
      throw new ApiError("Source document request timed out — try again.", null);
    }
    throw new ApiError("Could not open the source document.", null);
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export const postClaimQuoteVerification = (
  caseId: string,
  claimId: string,
  body: { reasoning: string },
): Promise<ClaimResponse> =>
  post(
    casePath(caseId, `/claims/${encodeURIComponent(claimId)}/verify-quote`),
    body,
  );

export const postConflict = (body: {
  claimAId: string;
  claimBId: string;
  conflictType: string;
  severity: string;
  summary: string;
}): Promise<ConflictResponse> => post("/conflicts", body);

export const postCaseConflict = (
  caseId: string,
  body: {
    claimAId: string;
    claimBId: string;
    conflictType: string;
    severity: string;
    summary: string;
  },
): Promise<ConflictResponse> => post(casePath(caseId, "/conflicts"), body);

export const postSection = (body: {
  title: string;
  paragraphRef: string;
  text: string;
  claimIds: string[];
}): Promise<SectionResponse> => post("/report-sections", body);

export const postCaseSection = (
  caseId: string,
  body: {
    title: string;
    paragraphRef: string;
    text: string;
    claimIds: string[];
  },
): Promise<SectionResponse> => post(casePath(caseId, "/report-sections"), body);

export const putSection = (
  sectionId: string,
  body: {
    title: string;
    paragraphRef: string;
    text: string;
    claimIds: string[];
    expectedVersion: number;
  },
): Promise<SectionResponse> =>
  request(`/report-sections/${sectionId}`, { method: "PUT", body: JSON.stringify(body) });

export const putCaseSection = (
  caseId: string,
  sectionId: string,
  body: {
    title: string;
    paragraphRef: string;
    text: string;
    claimIds: string[];
    expectedVersion: number;
  },
): Promise<SectionResponse> =>
  request(casePath(caseId, `/report-sections/${encodeURIComponent(sectionId)}`), {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const approveSection = (
  sectionId: string,
  expectedVersion: number,
): Promise<SectionResponse> =>
  post(
    `/report-sections/${sectionId}/approve?expectedVersion=${encodeURIComponent(expectedVersion)}`,
    {},
  );

export const approveCaseSection = (
  caseId: string,
  sectionId: string,
  expectedVersion: number,
): Promise<SectionResponse> =>
  post(
    casePath(
      caseId,
      `/report-sections/${encodeURIComponent(sectionId)}/approve?expectedVersion=${encodeURIComponent(expectedVersion)}`,
    ),
    {},
  );

export interface ServerPacketEntry {
  sectionId: string;
  revisionId?: string | null;
  title: string;
  paragraphRef: string;
  impactStatus: string;
  disposition: string;
  reason: string;
}

/** A packet built, redacted, rendered, and hash-stamped by the server. */
export interface ServerPacket {
  packetId: string;
  packetType: string;
  generatedAt: string;
  generatedByName: string;
  generatedByRole: string;
  stats: { included: number; excluded: number; withheld: number };
  entries: ServerPacketEntry[];
  sha256: string;
  packetIntegrityHash: string;
  pdfSha256: string | null;
  hasPdf: boolean;
  pdfFilename: string | null;
  filename: string;
  document: string;
  auditEvent: AuditEvent;
  replayed: boolean;
}

export interface PacketArtifactSummary {
  packetId: string;
  caseId: string;
  packetType: string;
  generatedAt: string;
  generatedByReviewerId: string | null;
  generatedByName: string | null;
  generatedByRole: string | null;
  filename: string;
  bodySha256: string;
  documentSha256: string;
  manifestSha256: string;
  artifactSha256: string;
  pdfSha256: string | null;
  hasPdf: boolean;
  pdfFilename: string | null;
  packetIntegrityHash: string | null;
  stats: { included: number; excluded: number; withheld: number };
  integrityOk: boolean;
}

export interface PacketArtifactList {
  caseId: string;
  total: number;
  limit: number;
  offset: number;
  verification: {
    ok: boolean;
    rootIntegrityHash: string | null;
    checked: number | null;
    scope: "page-artifacts";
  };
  packets: PacketArtifactSummary[];
}

export interface PacketArtifactDetail extends PacketArtifactSummary {
  manifest: {
    entries?: Array<{
      sectionId: string;
      revisionId?: string | null;
      revisionContentSha256?: string | null;
      title?: string;
      paragraphRef?: string;
      claimIds?: string[];
      impactStatus: string;
      disposition: string;
      reason: string;
    }>;
    [key: string]: unknown;
  };
  document: string;
  verification: {
    ok: boolean;
    packetIssues: Array<{ id: string; kind: string }>;
    chain: {
      ok: boolean;
      scope?: "artifact";
      issues: Array<{ id: string; kind: string }>;
    };
  };
}

export const generatePacket = (body: { packetType: string }): Promise<ServerPacket> =>
  post("/packets", body, PACKET_GENERATION_TIMEOUT_MS);

export const generateCasePacket = (
  caseId: string,
  body: { packetType: string; idempotencyKey?: string },
): Promise<ServerPacket> =>
  post(casePath(caseId, "/packets"), body, PACKET_GENERATION_TIMEOUT_MS);

export const fetchCasePackets = (
  caseId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<PacketArtifactList> => {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return request(casePath(caseId, `/packets${suffix}`));
};

export const fetchCasePacket = (
  caseId: string,
  packetId: string,
): Promise<PacketArtifactDetail> =>
  request(casePath(caseId, `/packets/${encodeURIComponent(packetId)}`));

export const isUnauthorized = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 401;

export const isConflict = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 409;

/** MFA validation is a retryable 422; retain 401 compatibility with older servers. */
export const isInvalidMfaCode = (error: unknown): boolean =>
  error instanceof ApiError &&
  (error.status === 401 || error.status === 422) &&
  error.message === "Invalid MFA code.";

export const fetchAdminReviewers = (): Promise<{ reviewers: AdminReviewer[] }> =>
  request("/admin/reviewers");

export const fetchAdminAccountAudit = (): Promise<{ events: AccountAuditEvent[] }> =>
  request("/admin/account-audit");

export const postAdminReviewer = (body: {
  name: string;
  username: string;
  role: string;
  initialPassword: string;
}): Promise<AdminReviewerResponse> => post("/admin/reviewers", body);

export const postAdminReviewerActive = (
  reviewerId: string,
  isActive: boolean,
): Promise<AdminReviewerResponse> =>
  post(
    `/admin/reviewers/${encodeURIComponent(reviewerId)}/${
      isActive ? "reactivate" : "deactivate"
    }`,
    {},
  );

export const postAdminReviewerPassword = (
  reviewerId: string,
  body: { newPassword: string },
): Promise<AdminReviewerResponse> =>
  post(`/admin/reviewers/${encodeURIComponent(reviewerId)}/reset-password`, body);

export const postAdminReviewerMfaReset = (
  reviewerId: string,
): Promise<AdminReviewerResponse> =>
  post(`/admin/reviewers/${encodeURIComponent(reviewerId)}/reset-mfa`, {});

export const fetchCaseMembers = (
  caseId: string,
): Promise<{ members: AdminCaseMember[] }> =>
  request(`/admin/cases/${encodeURIComponent(caseId)}/members`);

export const postCaseMember = (
  caseId: string,
  body: { reviewerId: string; role?: string },
): Promise<AdminCaseMemberResponse> =>
  post(`/admin/cases/${encodeURIComponent(caseId)}/members`, body);

export const postCaseMemberActive = (
  caseId: string,
  reviewerId: string,
  isActive: boolean,
): Promise<AdminCaseMemberResponse> =>
  post(
    `/admin/cases/${encodeURIComponent(caseId)}/members/${encodeURIComponent(
      reviewerId,
    )}/${isActive ? "reactivate" : "deactivate"}`,
    {},
  );

export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Unexpected error contacting the case service.";
}
