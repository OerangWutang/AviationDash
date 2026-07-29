import { useStore } from "../../state/store";
import { formatDateTime } from "../../lib/format";
import { SourceDocumentCard } from "../SourceDocumentCard";
import { ClaimStatusBadge } from "../StatusBadge";
import { NewClaimForm } from "../NewClaimForm";
import { SourceUploadForm } from "../SourceUploadForm";

export function DocumentsView() {
  const { state, selectConflict, selectClaim, selectView, switchCase } = useStore();
  const sources = [...state.sources.values()];
  const claims = [...state.claims.values()];
  // Upload exists only in server-authoritative mode; local demo mode has no
  // document store to put anything in.
  const serverMode = state.dataMode === "server" && state.caseFile !== null;
  const matterRole = state.caseMembership?.role ?? null;

  return (
    <div className="view-stack">
      <header className="view-header">
        <h2>Documents</h2>
        <p className="muted">
          Every ingested source with privilege designation, hash, and chain of custody.
          Documents are never deleted from the record.
        </p>
      </header>
      {serverMode && state.caseFile && (
        <section className="panel">
          <SourceUploadForm
            caseId={state.caseFile.id}
            canUploadPrivileged={matterRole === "Senior Aviation Counsel"}
            // Reload the matter so the new source and its pages appear.
            // Re-selecting the current matter is the existing load path, so
            // this reuses the store's epoch/staleness handling rather than
            // inventing a second way to mutate case state.
            onUploaded={() => {
              if (state.caseFile) void switchCase(state.caseFile.id);
            }}
          />
        </section>
      )}
      {sources.map((source) => {
        const citing = claims.filter((c) => c.sourceDocumentId === source.id);
        return (
          <article key={source.id} className="panel doc-entry">
            <SourceDocumentCard source={source} />
            <details className="custody-details">
              <summary>Chain of custody ({source.custody.length} events)</summary>
              <ol className="custody-list">
                {source.custody.map((event, i) => (
                  <li key={i} className="custody-item">
                    <span className="mono custody-time">{formatDateTime(event.at)}</span>
                    <span className="custody-body">
                      <strong>{event.actor}</strong> — {event.action}
                    </span>
                  </li>
                ))}
              </ol>
            </details>
            <NewClaimForm source={source} />
            {citing.length > 0 && (
              <div className="doc-claims">
                <h4 className="rail-title">Claims extracted from this document</h4>
                <ul className="doc-claim-list">
                  {citing.map((claim) => (
                    <li key={claim.id}>
                      <button
                        type="button"
                        className="doc-claim-link"
                        title={
                          claim.relatedConflictIds.length > 0
                            ? "Open in conflict review"
                            : "Open in claims ledger"
                        }
                        onClick={() => {
                          if (claim.relatedConflictIds.length > 0) {
                            selectConflict(claim.relatedConflictIds[0]);
                            selectClaim(claim.id);
                          } else {
                            selectClaim(claim.id);
                            selectView("claims");
                          }
                        }}
                      >
                        <ClaimStatusBadge status={claim.status} />
                        <span>{claim.text}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}
