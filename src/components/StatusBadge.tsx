import type { ReactNode } from "react";
import type {
  ClaimStatus,
  ConflictSeverity,
  ConflictStatus,
  ReportEligibility,
} from "../domain/types";
import type { SectionStatus } from "../domain/report";
import {
  claimStatusLabel,
  claimStatusTone,
  conflictStatusLabel,
  conflictStatusTone,
  eligibilityLabel,
  eligibilityTone,
  sectionStatusLabel,
  sectionStatusTone,
  severityTone,
  type Tone,
} from "../lib/format";

export function Badge({
  tone,
  children,
  title,
}: {
  tone: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function ClaimStatusBadge({ status }: { status: ClaimStatus }) {
  return <Badge tone={claimStatusTone[status]}>{claimStatusLabel(status)}</Badge>;
}

export function ConflictStatusBadge({ status }: { status: ConflictStatus }) {
  return <Badge tone={conflictStatusTone[status]}>{conflictStatusLabel(status)}</Badge>;
}

export function EligibilityBadge({ eligibility }: { eligibility: ReportEligibility }) {
  return (
    <Badge tone={eligibilityTone[eligibility]} title="Report eligibility">
      {eligibilityLabel(eligibility)}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: ConflictSeverity }) {
  return (
    <Badge tone={severityTone[severity]} title="Conflict severity">
      {severity}
    </Badge>
  );
}

export function SectionStatusBadge({ status }: { status: SectionStatus }) {
  return <Badge tone={sectionStatusTone[status]}>{sectionStatusLabel(status)}</Badge>;
}

export function SeverityDot({ severity }: { severity: ConflictSeverity }) {
  return <span className={`sev-dot sev-${severity}`} aria-hidden="true" />;
}
