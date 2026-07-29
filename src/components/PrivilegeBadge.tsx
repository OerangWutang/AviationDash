import type { PrivilegeStatus } from "../domain/types";
import { WITHHOLDING_PRIVILEGE } from "../domain/types";
import { privilegeLabel, privilegeTone } from "../lib/format";
import { Badge } from "./StatusBadge";

function LockGlyph() {
  return (
    <svg
      className="lock-glyph"
      viewBox="0 0 12 12"
      width="10"
      height="10"
      aria-hidden="true"
    >
      <rect x="2" y="5" width="8" height="6" rx="1" fill="currentColor" />
      <path
        d="M4 5V3.5a2 2 0 0 1 4 0V5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
    </svg>
  );
}

export function PrivilegeBadge({ status }: { status: PrivilegeStatus }) {
  const withholding = WITHHOLDING_PRIVILEGE.includes(status);
  return (
    <Badge
      tone={privilegeTone[status]}
      title={
        withholding
          ? "Privileged: withheld from report use regardless of review outcome"
          : "Privilege status"
      }
    >
      {withholding && <LockGlyph />}
      {privilegeLabel(status)}
    </Badge>
  );
}
