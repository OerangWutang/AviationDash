import { useId, useRef, useState } from "react";

import { ApiError, postCaseSource } from "../api/client";
import type { PrivilegeStatus, SourceDocument, SourceType } from "../domain/types";

const SOURCE_TYPES: { value: SourceType; label: string }[] = [
  { value: "official_report", label: "Official report" },
  { value: "flight_recorder", label: "Flight recorder" },
  { value: "deposition", label: "Deposition" },
  { value: "expert_report", label: "Expert report" },
  { value: "operational_record", label: "Operational record" },
  { value: "atc_transcript", label: "ATC transcript" },
  { value: "weather_data", label: "Weather data" },
  { value: "internal_memo", label: "Internal memo" },
];

const PRIVILEGE_STATUSES: { value: PrivilegeStatus; label: string }[] = [
  { value: "public", label: "Public" },
  { value: "confidential", label: "Confidential" },
  { value: "attorney_client", label: "Attorney-client (withheld)" },
  { value: "work_product", label: "Work product (withheld)" },
  { value: "restricted", label: "Restricted (withheld)" },
];

const WITHHOLDING: PrivilegeStatus[] = [
  "attorney_client",
  "work_product",
  "restricted",
];

async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  // Chunked so a large document does not blow the argument limit of
  // String.fromCharCode on one call.
  let binary = "";
  const CHUNK = 0x8000;
  for (let index = 0; index < bytes.length; index += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(index, index + CHUNK));
  }
  return btoa(binary);
}

export function SourceUploadForm({
  caseId,
  canUploadPrivileged,
  onUploaded,
}: {
  caseId: string;
  /** Whether this reviewer's matter role may upload withheld material. The
   *  server enforces this; the form explains it rather than letting a reviewer
   *  fill everything in and then be refused. */
  canUploadPrivileged: boolean;
  onUploaded: (source: SourceDocument, duplicates: string[]) => void;
}) {
  const formId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [type, setType] = useState<SourceType>("official_report");
  const [origin, setOrigin] = useState("");
  const [custodian, setCustodian] = useState("");
  const [docketRef, setDocketRef] = useState("");
  const [privilegeStatus, setPrivilegeStatus] = useState<PrivilegeStatus>("public");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Generated once per attempt and reused across retries of that same attempt,
  // so a retried upload returns the original result instead of creating a
  // second copy of the same document.
  const idempotencyKey = useRef<string | null>(null);

  const privilegedBlocked =
    WITHHOLDING.includes(privilegeStatus) && !canUploadPrivileged;
  const ready =
    file !== null &&
    title.trim() !== "" &&
    origin.trim() !== "" &&
    custodian.trim() !== "" &&
    !privilegedBlocked;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !ready || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    idempotencyKey.current ??= crypto.randomUUID();

    try {
      const response = await postCaseSource(caseId, {
        title: title.trim(),
        type,
        origin: origin.trim(),
        custodian: custodian.trim(),
        docketRef: docketRef.trim() || null,
        privilegeStatus,
        originalFilename: file.name,
        contentBase64: await fileToBase64(file),
        idempotencyKey: idempotencyKey.current,
      });

      const duplicates = response.possibleDuplicateSourceIds;
      if (duplicates.length > 0) {
        setNotice(
          `A document with identical content is already in this matter (${duplicates.join(", ")}). ` +
            `This upload was kept as a separate record with its own custody history — ` +
            `the same document can legitimately arrive through a different custodian or production.`,
        );
      }
      idempotencyKey.current = null;
      setFile(null);
      setTitle("");
      setOrigin("");
      setCustodian("");
      setDocketRef("");
      onUploaded(response.source, duplicates);
    } catch (caught) {
      // The key is kept on failure so a retry is recognised as the same
      // upload rather than a new one.
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Upload failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="source-upload" onSubmit={submit}>
      <h3>Upload a source document</h3>
      <p className="source-upload__hint">
        PDF only. Each page is read from the document's own text layer where it
        has one, and by OCR where it does not. Long scanned documents can take
        a few minutes.
      </p>

      <label htmlFor={`${formId}-file`}>Document</label>
      <input
        id={`${formId}-file`}
        type="file"
        accept="application/pdf,.pdf"
        disabled={busy}
        onChange={(event) => {
          const selected = event.target.files?.[0] ?? null;
          setFile(selected);
          // A new file is a new upload, not a retry of the previous one.
          idempotencyKey.current = null;
          if (selected && !title.trim()) {
            setTitle(selected.name.replace(/\.pdf$/i, ""));
          }
        }}
      />

      <label htmlFor={`${formId}-title`}>Title</label>
      <input
        id={`${formId}-title`}
        value={title}
        disabled={busy}
        maxLength={300}
        onChange={(event) => setTitle(event.target.value)}
      />

      <label htmlFor={`${formId}-type`}>Type</label>
      <select
        id={`${formId}-type`}
        value={type}
        disabled={busy}
        onChange={(event) => setType(event.target.value as SourceType)}
      >
        {SOURCE_TYPES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <label htmlFor={`${formId}-origin`}>Origin</label>
      <input
        id={`${formId}-origin`}
        value={origin}
        disabled={busy}
        maxLength={200}
        placeholder="NTSB, opposing counsel, operator…"
        onChange={(event) => setOrigin(event.target.value)}
      />

      <label htmlFor={`${formId}-custodian`}>Custodian</label>
      <input
        id={`${formId}-custodian`}
        value={custodian}
        disabled={busy}
        maxLength={200}
        onChange={(event) => setCustodian(event.target.value)}
      />

      <label htmlFor={`${formId}-docket`}>Docket reference (optional)</label>
      <input
        id={`${formId}-docket`}
        value={docketRef}
        disabled={busy}
        maxLength={120}
        onChange={(event) => setDocketRef(event.target.value)}
      />

      <label htmlFor={`${formId}-privilege`}>Privilege status</label>
      <select
        id={`${formId}-privilege`}
        value={privilegeStatus}
        disabled={busy}
        onChange={(event) =>
          setPrivilegeStatus(event.target.value as PrivilegeStatus)
        }
      >
        {PRIVILEGE_STATUSES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      {privilegedBlocked && (
        <p className="source-upload__blocked" role="status">
          Uploading attorney-client, work-product, or restricted material
          requires a Senior Aviation Counsel role in this matter.
        </p>
      )}

      <p className="source-upload__hint">
        Custody is recorded by the server from your identity, matter role, the
        time, and the document's hash — it is not taken from this form.
      </p>

      <button type="submit" disabled={!ready || busy}>
        {busy ? "Uploading and reading pages…" : "Upload document"}
      </button>

      {busy && (
        <p className="source-upload__hint" role="status">
          Reading the document. Leaving this page will cancel the upload.
        </p>
      )}
      {error && (
        <p className="source-upload__error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="source-upload__notice" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}
