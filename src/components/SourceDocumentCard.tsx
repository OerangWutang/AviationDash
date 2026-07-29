import type { SourceDocument } from "../domain/types";
import { formatDateTime, sourceTypeLabel, truncateHash } from "../lib/format";
import { PrivilegeBadge } from "./PrivilegeBadge";

export function SourceDocumentCard({ source }: { source: SourceDocument }) {
  return (
    <div className="source-card">
      <div className="source-card-head">
        <span className="source-title">{source.title}</span>
        <PrivilegeBadge status={source.privilegeStatus} />
      </div>
      <dl className="meta-grid">
        <dt>Type</dt>
        <dd>{sourceTypeLabel(source.type)}</dd>
        <dt>Origin</dt>
        <dd>{source.origin}</dd>
        <dt>Custodian</dt>
        <dd>{source.custodian}</dd>
        <dt>Docket ref</dt>
        <dd>{source.docketRef ?? "— (not docketed)"}</dd>
        <dt>Ingested</dt>
        <dd className="mono">{formatDateTime(source.ingestedAt)}</dd>
        <dt>SHA-256</dt>
        <dd className="mono" title={source.sha256}>
          {truncateHash(source.sha256)}
        </dd>
      </dl>
    </div>
  );
}
