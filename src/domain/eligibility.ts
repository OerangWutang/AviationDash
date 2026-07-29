import type { Claim, Conflict, ReportEligibility } from "./types";
import { WITHHOLDING_PRIVILEGE } from "./types";

/**
 * Compute a claim's report eligibility from its own state plus the state of
 * every conflict it participates in.
 *
 * Rule order is doctrine, not style:
 *  1. Privilege withholds — attorney-client / work-product / restricted
 *     material can never become report-eligible, whatever the review outcome.
 *  2. Rejected or superseded claims are excluded from reports (but remain
 *     visible in the record).
 *  3. Any unresolved conflict blocks report use.
 *  4. An escalated conflict parks the claim pending senior review.
 *  5. Only affirmatively supported or preserved claims are eligible.
 *  6. Everything else (unreviewed, disputed, escalated) still needs review.
 */
export function computeClaimEligibility(
  claim: Pick<Claim, "status" | "privilegeStatus" | "relatedConflictIds">,
  conflictsById: ReadonlyMap<string, Conflict>,
): ReportEligibility {
  if (WITHHOLDING_PRIVILEGE.includes(claim.privilegeStatus)) {
    return "privileged";
  }
  if (claim.status === "rejected" || claim.status === "superseded") {
    return "excluded";
  }
  const related = claim.relatedConflictIds
    .map((id) => conflictsById.get(id))
    .filter((c): c is Conflict => c !== undefined);
  if (related.some((c) => c.status === "unresolved")) {
    return "blocked_by_conflict";
  }
  if (related.some((c) => c.status === "escalated")) {
    return "needs_review";
  }
  if (claim.status === "supported" || claim.status === "preserved") {
    return "eligible";
  }
  return "needs_review";
}
