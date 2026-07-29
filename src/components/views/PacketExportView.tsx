import { useEffect, useRef, useState } from "react";
import {
  buildEvidencePacket,
  packetFilename,
  packetPermission,
  renderPacketBody,
  renderPacketDocument,
  sha256Hex,
  type PacketType,
} from "../../domain/packet";
import type { PacketArtifactSummary } from "../../api/client";
import type { SectionStatus } from "../../domain/report";
import { effectiveMatterRole, useStore } from "../../state/store";
import { formatDateTime, truncateHash } from "../../lib/format";
import { SectionStatusBadge } from "../StatusBadge";

/** What the manifest/preview needs, whether the packet was assembled locally
 *  or by the server. */
interface GeneratedView {
  document: string;
  sha256: string;
  filename: string;
  packetId: string;
  type: string;
  generatedAt: string;
  generatedByName: string | null;
  generatedByRole: string | null;
  stats: { included: number; excluded: number; withheld: number };
  entries: {
    key: string;
    title: string;
    paragraphRef: string;
    impactStatus: SectionStatus;
    disposition: string;
    reason: string;
  }[];
}

interface QuarantinedPacket {
  packetId: string;
  filename: string;
  issues: string[];
}

const PACKET_CHOICES: { type: PacketType; label: string; description: string }[] = [
  {
    type: "internal",
    label: "Internal work-product packet",
    description:
      "Complete review state for the case team: all sections (whatever their status), privileged material marked DO NOT PRODUCE, internal assessments, and the full decision log with reasoning.",
  },
  {
    type: "production",
    label: "Production packet (external)",
    description:
      "Only report-eligible sections. Privileged material is withheld into a privilege log; blocked sections are excluded and identified in the manifest; internal assessments and decision reasoning are stripped as work product.",
  },
];

export function PacketExportView() {
  const {
    state,
    recordPacketExport,
    generateServerPacket,
    fetchPacketArtifacts,
    fetchPacketArtifact,
  } = useStore();
  const reviewer = state.reviewers.find((r) => r.id === state.activeReviewerId);
  const matterRole = effectiveMatterRole(state);
  const [packetType, setPacketType] = useState<PacketType>("internal");
  const [generated, setGenerated] = useState<GeneratedView | null>(null);
  const [quarantined, setQuarantined] = useState<QuarantinedPacket | null>(null);
  const [generating, setGenerating] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [pastPackets, setPastPackets] = useState<{
    total: number;
    items: PacketArtifactSummary[];
    verificationOk: boolean;
  }>({ total: 0, items: [], verificationOk: true });
  const [loadingPast, setLoadingPast] = useState(false);
  const [pastError, setPastError] = useState<string | null>(null);
  const [loadingPacketId, setLoadingPacketId] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const pastListRequestRef = useRef(0);
  const packetDetailRequestRef = useRef(0);

  const loadPastPackets = async () => {
    if (state.dataMode !== "server" || !reviewer || !matterRole) return;
    const requestId = ++pastListRequestRef.current;
    setLoadingPast(true);
    try {
      const outcome = await fetchPacketArtifacts({ limit: 25, offset: 0 });
      if (pastListRequestRef.current !== requestId) return;
      if (outcome.ok) {
        setPastPackets({
          total: outcome.result.total,
          items: outcome.result.packets,
          verificationOk: outcome.result.verification.ok,
        });
        setPastError(null);
      } else {
        setPastError(outcome.error);
      }
    } finally {
      if (pastListRequestRef.current === requestId) setLoadingPast(false);
    }
  };

  useEffect(() => {
    void loadPastPackets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.dataMode, state.caseFile.id]);

  if (!reviewer || !matterRole) return null;

  const selectedPermission = packetPermission(packetType, matterRole);

  const generateLocally = async (): Promise<GeneratedView | string> => {
    const packet = buildEvidencePacket({
      caseFile: state.caseFile,
      sections: state.reportSections,
      claimsById: state.claims,
      conflictsById: state.conflicts,
      sourcesById: state.sources,
      decisions: [...state.decisions.values()],
      meta: {
        packetId: crypto.randomUUID(),
        type: packetType,
        generatedAt: new Date().toISOString(),
        generatedByName: reviewer.name,
        generatedByRole: matterRole,
      },
    });
    const body = renderPacketBody(packet);
    const sha256 = await sha256Hex(body);
    // The packet only counts as generated once it is on the audit trail.
    const outcome = await recordPacketExport({
      packetId: packet.meta.packetId,
      packetType: packet.meta.type,
      included: packet.stats.included,
      excluded: packet.stats.excluded,
      withheld: packet.stats.withheld,
      sha256,
    });
    if (!outcome.ok) return outcome.error;
    return {
      document: renderPacketDocument(packet, body, sha256),
      sha256,
      filename: packetFilename(packet),
      packetId: packet.meta.packetId,
      type: packet.meta.type,
      generatedAt: packet.meta.generatedAt,
      generatedByName: packet.meta.generatedByName,
      generatedByRole: packet.meta.generatedByRole,
      stats: packet.stats,
      entries: packet.entries.map((entry) => ({
        key: entry.section.id,
        title: entry.section.title,
        paragraphRef: entry.section.paragraphRef,
        impactStatus: entry.impact.status,
        disposition: entry.disposition,
        reason: entry.reason,
      })),
    };
  };

  const generateOnServer = async (): Promise<GeneratedView | string> => {
    const outcome = await generateServerPacket(packetType);
    if (!outcome.ok) return outcome.error;
    const packet = outcome.packet;
    return {
      document: packet.document,
      sha256: packet.sha256,
      filename: packet.filename,
      packetId: packet.packetId,
      type: packet.packetType,
      generatedAt: packet.generatedAt,
      generatedByName: packet.generatedByName,
      generatedByRole: packet.generatedByRole,
      stats: packet.stats,
      entries: packet.entries.map((entry) => ({
        key: entry.sectionId,
        title: entry.title,
        paragraphRef: entry.paragraphRef,
        impactStatus: entry.impactStatus as SectionStatus,
        disposition: entry.disposition,
        reason: entry.reason,
      })),
    };
  };

  const handleGenerate = async () => {
    if (!selectedPermission.allowed) {
      setExportError(selectedPermission.reason);
      return;
    }
    setGenerating(true);
    try {
      const result =
        state.dataMode === "server" ? await generateOnServer() : await generateLocally();
      if (typeof result === "string") {
        setExportError(result);
      } else {
        setGenerated(result);
        setQuarantined(null);
        setExportError(null);
        await loadPastPackets();
      }
    } finally {
      setGenerating(false);
    }
  };

  const handleDownload = () => {
    if (!generated) return;
    const blob = new Blob([generated.document], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = generated.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const handlePrint = () => {
    frameRef.current?.contentWindow?.print();
  };

  const handleOpenStored = async (packetId: string) => {
    const requestId = ++packetDetailRequestRef.current;
    setLoadingPacketId(packetId);
    try {
      const outcome = await fetchPacketArtifact(packetId);
      if (packetDetailRequestRef.current !== requestId) return;
      if (!outcome.ok) {
        setPastError(outcome.error);
        return;
      }
      const packet = outcome.packet;
      if (!packet.verification.ok) {
        const issues = [
          ...packet.verification.packetIssues.map(
            (issue) => `Packet ${issue.id}: ${issue.kind}`,
          ),
          ...packet.verification.chain.issues.map(
            (issue) => `Integrity chain ${issue.id}: ${issue.kind}`,
          ),
        ];
        setGenerated(null);
        setQuarantined({
          packetId: packet.packetId,
          filename: packet.filename,
          issues: issues.length > 0 ? issues : ["Integrity verification failed."],
        });
        setPastError(null);
        return;
      }
      const entries = packet.manifest.entries ?? [];
      setGenerated({
        document: packet.document,
        sha256: packet.bodySha256,
        filename: packet.filename,
        packetId: packet.packetId,
        type: packet.packetType,
        generatedAt: packet.generatedAt,
        generatedByName: packet.generatedByName,
        generatedByRole: packet.generatedByRole,
        stats: packet.stats,
        entries: entries.map((entry) => ({
          key: entry.sectionId,
          title: entry.title ?? entry.sectionId,
          paragraphRef: entry.paragraphRef ?? entry.sectionId,
          impactStatus: entry.impactStatus as SectionStatus,
          disposition: entry.disposition,
          reason: entry.reason,
        })),
      });
      setQuarantined(null);
      setPastError(null);
    } finally {
      if (packetDetailRequestRef.current === requestId) setLoadingPacketId(null);
    }
  };

  return (
    <div className="view-stack wide">
      <header className="view-header">
        <h2>Evidence packet</h2>
        <p className="muted">
          Assemble the case's report sections and supporting evidence into a
          self-contained, hash-stamped document.
          {state.dataMode === "server"
            ? " Packets are built and redacted by the case service — the disclosure rules run server-side."
            : " Generation is recorded in the audit trail."}{" "}
          Excluded material is always identified in the manifest — never silently
          dropped.
        </p>
      </header>

      <section className="panel" aria-label="Packet options">
        <h3 className="section-title">Packet type</h3>
        <fieldset className="decision-options">
          <legend className="visually-hidden">Packet type</legend>
          {PACKET_CHOICES.map(({ type, label, description }) => {
            const permission = packetPermission(type, matterRole);
            return (
              <label
                key={type}
                className={`decision-option${!permission.allowed ? " disabled" : ""}${
                  packetType === type ? " selected" : ""
                }`}
              >
                <input
                  type="radio"
                  name="packet-type"
                  value={type}
                  checked={packetType === type}
                  disabled={!permission.allowed}
                  onChange={() => setPacketType(type)}
                />
                <span className="decision-option-body">
                  <span className="decision-option-label">{label}</span>
                  <span className="decision-option-desc">
                    {permission.allowed ? description : permission.reason}
                  </span>
                </span>
              </label>
            );
          })}
        </fieldset>
        <div className="save-row">
          <button
            type="button"
            className="btn-primary"
            disabled={generating || !selectedPermission.allowed}
            onClick={() => void handleGenerate()}
          >
            {generating ? "Generating…" : "Generate packet"}
          </button>
          <span className="save-blocker">
            Acting as {reviewer.name} · {matterRole}. The packet reflects case state
            at generation time — regenerate after new decisions.
          </span>
        </div>
        {exportError && <p className="form-error">{exportError}</p>}
      </section>

      {state.dataMode === "server" && (
        <section className="panel" aria-label="Past packets">
          <h3 className="section-title">Past packets</h3>
          {loadingPast ? (
            <p className="muted empty-note">Loading stored packets…</p>
          ) : pastPackets.items.length === 0 ? (
            <p className="muted empty-note">No stored packets for this matter.</p>
          ) : (
            <>
              <p className={pastPackets.verificationOk ? "save-confirm" : "form-error"}>
                {pastPackets.total} stored packet(s). Integrity chain{" "}
                {pastPackets.verificationOk ? "verified" : "has verification issues"}.
              </p>
              <ul className="impact-list manifest-list">
                {pastPackets.items.map((packet) => (
                  <li key={packet.packetId} className="impact-item">
                    <div className="impact-head">
                      <span>
                        <strong>{packet.packetType}</strong>{" "}
                        <span className="mono muted">{formatDateTime(packet.generatedAt)}</span>
                      </span>
                      <span className="badge-row">
                        <span
                          className={`badge ${
                            packet.integrityOk ? "tone-positive" : "tone-negative"
                          }`}
                        >
                          {packet.integrityOk ? "verified" : "failed"}
                        </span>
                      </span>
                    </div>
                    <p className="impact-note">
                      {packet.stats.included} included, {packet.stats.excluded} excluded,{" "}
                      {packet.stats.withheld} withheld · SHA-256{" "}
                      <span className="mono" title={packet.bodySha256}>
                        {truncateHash(packet.bodySha256)}
                      </span>
                    </p>
                    <div className="save-row">
                      <button
                        type="button"
                        className="btn-secondary compact"
                        disabled={
                          !packet.integrityOk || loadingPacketId === packet.packetId
                        }
                        onClick={() => void handleOpenStored(packet.packetId)}
                      >
                        {!packet.integrityOk
                          ? "Quarantined"
                          : loadingPacketId === packet.packetId
                            ? "Opening…"
                            : "Open stored packet"}
                      </button>
                      {!packet.integrityOk && (
                        <span className="save-blocker">
                          Integrity failed; opening, download, and print are disabled.
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          {pastError && <p className="form-error">{pastError}</p>}
        </section>
      )}

      {quarantined && (
        <section className="panel" aria-label="Quarantined packet" role="alert">
          <h3 className="section-title">Packet quarantined</h3>
          <p className="form-error">
            Stored packet <span className="mono">{quarantined.filename}</span> failed
            integrity verification and was not opened. Download, preview, and print are
            disabled.
          </p>
          <p className="muted mono">Packet {quarantined.packetId}</p>
          <ul className="impact-list">
            {quarantined.issues.map((issue) => (
              <li key={issue} className="impact-item">
                {issue}
              </li>
            ))}
          </ul>
        </section>
      )}

      {generated && (
        <>
          <section className="panel" aria-label="Packet manifest">
            <h3 className="section-title">Manifest</h3>
            <p className="save-confirm" role="status">
              Packet generated: <strong>{generated.type}</strong>,{" "}
              {generated.stats.included} section(s) included, {generated.stats.excluded}{" "}
              excluded, {generated.stats.withheld} withheld under privilege. Generated{" "}
              {formatDateTime(generated.generatedAt)}
              {generated.generatedByName && generated.generatedByRole
                ? ` by ${generated.generatedByName} · ${generated.generatedByRole}.`
                : ". Generator identity withheld."}
              Body SHA-256{" "}
              <span className="mono" title={generated.sha256}>
                {truncateHash(generated.sha256)}
              </span>{" "}
              — recorded in the audit trail.
            </p>
            <ul className="impact-list manifest-list">
              {generated.entries.map((entry) => (
                <li key={entry.key} className="impact-item">
                  <div className="impact-head">
                    <span>
                      <strong>{entry.title}</strong>{" "}
                      <span className="mono muted">¶ {entry.paragraphRef}</span>
                    </span>
                    <span className="badge-row">
                      <SectionStatusBadge status={entry.impactStatus} />
                      <span
                        className={`badge ${
                          entry.disposition === "included"
                            ? "tone-positive"
                            : entry.disposition === "withheld"
                              ? "tone-privileged"
                              : "tone-neutral"
                        }`}
                      >
                        {entry.disposition}
                      </span>
                    </span>
                  </div>
                  <p className="impact-note">{entry.reason}</p>
                </li>
              ))}
            </ul>
            <div className="save-row">
              <button type="button" className="btn-primary" onClick={handleDownload}>
                Download packet (.html)
              </button>
              <button type="button" className="btn-secondary" onClick={handlePrint}>
                Print / save as PDF
              </button>
              <span className="save-blocker">
                The downloaded file is self-contained; the preview below is the same
                document, byte for byte.
              </span>
            </div>
          </section>

          <section className="panel packet-preview" aria-label="Packet preview">
            <h3 className="section-title">Preview</h3>
            <iframe
              ref={frameRef}
              className="packet-frame"
              title="Evidence packet preview"
              sandbox=""
              srcDoc={generated.document}
            />
          </section>
        </>
      )}
    </div>
  );
}
