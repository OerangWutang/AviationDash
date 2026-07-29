"""Server-authoritative evidence packet generation.

Mirrors ``src/domain/packet.ts`` — the disclosure doctrine must be enforced
where a tampered client cannot reach it:

- "internal": attorney work product for the case team. Everything included —
  all sections whatever their status, privileged material marked DO NOT
  PRODUCE, internal assessments, the full decision log with reasoning.
- "production": external-facing. Only ``eligible`` / ``eligible_with_
  disclosure`` sections; privileged material withheld in full into a
  privilege log (identity only, never content); blocked sections excluded and
  identified in the manifest; internal assessments (confidence, evidence
  quality, reviewer identities, decision reasoning) stripped as work product.
  Preserved conflicts are disclosed inline with both claims; a privileged
  disclosure counterpart is redacted and logged.

The rendered body's SHA-256 is embedded in the document footer and recorded
in the audit trail.
"""

from __future__ import annotations

import hashlib
import html
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .approval import revision_approval_is_current
from .db import models as m
from .db.seed import iso_z
from .domain.types import WITHHOLDING_PRIVILEGE

REDACTED_TEXT = "[Withheld — privileged material; see privilege log]"

e = html.escape


@dataclass
class PacketClaim:
    claim: m.Claim
    redacted: bool
    #: Packet-local reference used instead of the privileged claim's database
    #: identity in an external artifact.
    opaque_ref: str | None = None


@dataclass
class PacketDisclosure:
    conflict: m.Conflict
    claim_a: PacketClaim
    claim_b: PacketClaim


@dataclass
class PacketEntry:
    section: m.ReportSection
    #: Original snapshot citations retained only in process so privilege-log
    #: construction does not depend on the mutable section row. Never serialize.
    private_claim_ids: list[str]
    #: The section identifier safe to publish in this artifact. Production
    #: entries that are not included use a packet-local opaque reference.
    artifact_section_id: str
    revision_id: str | None
    revision_content_sha256: str | None
    #: Content actually rendered — for production packets this is the APPROVED
    #: revision's immutable snapshot, never the mutable ``section`` columns.
    title: str
    paragraph_ref: str
    text: str
    claim_ids: list[str]
    impact_status: str
    impact_note: str
    disposition: str  # included | excluded | withheld
    reason: str
    disclosures: list[PacketDisclosure] = field(default_factory=list)


@dataclass
class PrivilegeLogEntry:
    source: m.SourceDocument
    section_refs: list[str]


@dataclass
class EvidencePacket:
    case: m.CaseFile
    packet_id: str
    type: str
    generated_at: str
    generated_by_name: str
    generated_by_role: str
    entries: list[PacketEntry]
    cited_claims: list[PacketClaim]
    cited_sources: list[m.SourceDocument]
    privilege_log: list[PrivilegeLogEntry]
    decision_log: list[tuple[m.ReviewDecision, str]] | None
    stats: dict


def _is_withholding(claim: m.Claim) -> bool:
    return claim.privilege_status in WITHHOLDING_PRIVILEGE


def _internal_reason(status: str) -> str:
    return {
        "eligible": "Included — report-eligible.",
        "eligible_with_disclosure": "Included — usable with conflict disclosure.",
        "blocked": "Included for internal review — BLOCKED for report use (unresolved conflict).",
        "privileged_material": "Included for internal review — privileged; do not produce.",
        "needs_review": "Included for internal review — cited claims still need review.",
    }[status]


def build_packet(
    session: Session,
    packet_type: str,
    *,
    case_id: str,
    packet_id: str,
    now: str,
    generated_by_name: str,
    generated_by_role: str,
) -> EvidencePacket:
    # Local import to avoid a services↔packet cycle.
    from .services import _related_conflicts, _section_impact

    production = packet_type == "production"
    case = session.execute(select(m.CaseFile).where(m.CaseFile.id == case_id)).scalar_one()
    sections = list(
        session.execute(
            select(m.ReportSection)
            .where(m.ReportSection.case_id == case.id)
            .order_by(m.ReportSection.position)
        ).scalars()
    )

    withheld_counter = 0
    excluded_counter = 0
    redacted_claim_refs: dict[str, str] = {}

    def next_opaque_ref(disposition: str) -> str:
        nonlocal withheld_counter, excluded_counter
        if disposition == "withheld":
            withheld_counter += 1
            return f"WITHHELD-{withheld_counter:03d}"
        excluded_counter += 1
        return f"EXCLUDED-{excluded_counter:03d}"

    def packet_claim(claim: m.Claim) -> PacketClaim:
        redacted = production and _is_withholding(claim)
        opaque_ref = None
        if redacted:
            opaque_ref = redacted_claim_refs.get(claim.id)
            if opaque_ref is None:
                opaque_ref = next_opaque_ref("withheld")
                redacted_claim_refs[claim.id] = opaque_ref
        return PacketClaim(claim=claim, redacted=redacted, opaque_ref=opaque_ref)

    entries: list[PacketEntry] = []
    for section in sections:
        revision = (
            session.get(m.ReportSectionRevision, section.active_revision_id)
            if section.active_revision_id is not None
            else None
        )
        if revision is not None and (
            revision.section_id != section.id or revision.case_id != section.case_id
        ):
            revision = None
        # A production packet renders the APPROVED revision's immutable snapshot,
        # not the mutable ``section`` row: the manifest names a revision, so the
        # body must be that revision's content even if the section is later
        # changed out-of-band (bug, direct SQL, partial transaction). Internal
        # work product deliberately reflects the section's current state.
        approved = production and revision_approval_is_current(session, revision)
        source_ = revision if approved else section
        title = source_.title
        paragraph_ref = source_.paragraph_ref
        text = source_.text
        claim_ids = list(source_.claim_ids)
        private_claim_ids = list(claim_ids)
        impact = _section_impact(session, paragraph_ref, claim_ids)
        contains_privileged = any(
            (claim := session.get(m.Claim, claim_id)) is not None and _is_withholding(claim)
            for claim_id in claim_ids
        )
        if not production:
            disposition, reason = "included", _internal_reason(impact.status)
        elif contains_privileged:
            disposition, reason = (
                "withheld",
                "Withheld under privilege; see privilege log.",
            )
        elif not approved:
            disposition, reason = (
                "excluded",
                "Excluded from production.",
            )
        elif impact.status == "eligible":
            disposition, reason = "included", "Included — all cited claims report-eligible."
        elif impact.status == "eligible_with_disclosure":
            disposition, reason = "included", "Included with conflict disclosure."
        elif impact.status == "privileged_material":
            disposition, reason = (
                "withheld",
                "Withheld under privilege; see privilege log.",
            )
        else:
            disposition, reason = "excluded", "Excluded from production."

        disclosures: list[PacketDisclosure] = []
        if disposition == "included":
            seen: set[str] = set()
            for claim_id in claim_ids:
                claim = session.get(m.Claim, claim_id)
                if claim is None or claim.status != "preserved":
                    continue
                for conflict in _related_conflicts(session, claim.id):
                    if conflict.status != "preserved_both" or conflict.id in seen:
                        continue
                    seen.add(conflict.id)
                    claim_a = session.get(m.Claim, conflict.claim_a_id)
                    claim_b = session.get(m.Claim, conflict.claim_b_id)
                    if claim_a and claim_b:
                        claim_a_view = packet_claim(claim_a)
                        claim_b_view = packet_claim(claim_b)
                        disclosures.append(
                            PacketDisclosure(conflict, claim_a_view, claim_b_view)
                        )

        artifact_section_id = section.id
        revision_id = revision.id if revision is not None else None
        revision_content_sha256 = revision.content_sha256 if revision is not None else None
        impact_status = impact.status
        impact_note = impact.note
        if production and disposition != "included":
            opaque_ref = next_opaque_ref(disposition)
            artifact_section_id = opaque_ref
            revision_id = None
            revision_content_sha256 = None
            title = "Withheld material" if disposition == "withheld" else "Excluded material"
            paragraph_ref = opaque_ref
            text = ""
            claim_ids = []
            impact_status = (
                "privileged_material" if disposition == "withheld" else "needs_review"
            )
            impact_note = reason
        entries.append(
            PacketEntry(
                section=section,
                private_claim_ids=private_claim_ids,
                artifact_section_id=artifact_section_id,
                revision_id=revision_id,
                revision_content_sha256=revision_content_sha256,
                title=title,
                paragraph_ref=paragraph_ref,
                text=text,
                claim_ids=claim_ids,
                impact_status=impact_status,
                impact_note=impact_note,
                disposition=disposition,
                reason=reason,
                disclosures=disclosures,
            )
        )

    cited_by_id: dict[str, PacketClaim] = {}
    for entry in entries:
        if entry.disposition != "included":
            continue
        for claim_id in entry.claim_ids:
            claim = session.get(m.Claim, claim_id)
            if claim is not None:
                cited_by_id[claim.id] = packet_claim(claim)
        for disclosure in entry.disclosures:
            for pc in (disclosure.claim_a, disclosure.claim_b):
                if not pc.redacted:
                    cited_by_id[pc.claim.id] = pc
    cited_claims = sorted(cited_by_id.values(), key=lambda pc: pc.claim.id)

    # A redacted claim's source is privileged — privilege log, not appendix.
    source_ids = {pc.claim.source_document_id for pc in cited_claims if not pc.redacted}
    cited_sources = sorted(
        (s for sid in source_ids if (s := session.get(m.SourceDocument, sid)) is not None),
        key=lambda s: s.id,
    )

    privilege_log: list[PrivilegeLogEntry] = []
    if production:
        logged: dict[str, PrivilegeLogEntry] = {}

        def log(claim: m.Claim, opaque_ref: str) -> None:
            source = session.get(m.SourceDocument, claim.source_document_id)
            if source is None:
                return
            entry = logged.setdefault(source.id, PrivilegeLogEntry(source, []))
            if opaque_ref not in entry.section_refs:
                entry.section_refs.append(opaque_ref)

        for entry in entries:
            if entry.disposition == "withheld":
                # ``entry.claim_ids`` is deliberately empty for a sanitized
                # production entry; inspect the private source section only
                # while building the external-appropriate privilege log.
                for claim_id in entry.private_claim_ids:
                    claim = session.get(m.Claim, claim_id)
                    if claim is not None and _is_withholding(claim):
                        log(claim, entry.paragraph_ref)
            for disclosure in entry.disclosures:
                for pc in (disclosure.claim_a, disclosure.claim_b):
                    if pc.redacted and pc.opaque_ref is not None:
                        log(pc.claim, pc.opaque_ref)
        privilege_log = list(logged.values())

    decision_log = None
    if not production:
        decisions = session.execute(
            select(m.ReviewDecision)
            .join(m.Conflict, m.Conflict.id == m.ReviewDecision.conflict_id)
            .where(m.Conflict.case_id == case.id)
            .order_by(m.ReviewDecision.created_at.desc())
        ).scalars()
        decision_log = [
            (d, (c.summary if (c := session.get(m.Conflict, d.conflict_id)) else d.conflict_id))
            for d in decisions
        ]

    return EvidencePacket(
        case=case,
        packet_id=packet_id,
        type=packet_type,
        generated_at=now,
        generated_by_name=generated_by_name,
        generated_by_role=generated_by_role,
        entries=entries,
        cited_claims=cited_claims,
        cited_sources=cited_sources,
        privilege_log=privilege_log,
        decision_log=decision_log,
        stats={
            "included": sum(1 for x in entries if x.disposition == "included"),
            "excluded": sum(1 for x in entries if x.disposition == "excluded"),
            "withheld": sum(1 for x in entries if x.disposition == "withheld"),
        },
    )


# ── Rendering ─────────────────────────────────────────────────────────────


def _fmt(iso: str) -> str:
    return iso.replace("T", " ")[:16] + "Z" if "T" in iso else iso


def _label(value: str) -> str:
    return value.replace("_", " ")


def _render_citation(pc: PacketClaim, sources: dict[str, m.SourceDocument]) -> str:
    if pc.redacted:
        reference = f" ({pc.opaque_ref})" if pc.opaque_ref else ""
        return f'<p class="redacted">{e(REDACTED_TEXT + reference)}</p>'
    source = sources.get(pc.claim.source_document_id)
    source_line = (
        f"{e(source.title)}{f', {e(source.docket_ref)}' if source.docket_ref else ''}, "
        f"{e(pc.claim.page_ref)}"
        if source
        else e(pc.claim.page_ref)
    )
    return (
        f'<p class="claim-text">{e(pc.claim.text)}</p>\n'
        f'<p class="cite">{source_line}</p>\n'
        f"<blockquote>&ldquo;{e(pc.claim.quote)}&rdquo;</blockquote>"
    )


def _render_disclosure(d: PacketDisclosure, sources: dict[str, m.SourceDocument]) -> str:
    summary = (
        "Counterpart withheld under privilege"
        if d.claim_a.redacted or d.claim_b.redacted
        else d.conflict.summary
    )
    return f"""<div class="disclosure">
<p class="disclosure-head">Conflict disclosure — {e(summary)}</p>
<div class="disclosure-claim"><span class="side">Claim A</span>{_render_citation(d.claim_a, sources)}</div>
<div class="disclosure-claim"><span class="side">Claim B</span>{_render_citation(d.claim_b, sources)}</div>
<p class="disclosure-note">Both claims are preserved in the case record; neither has been suppressed.</p>
</div>"""


def render_packet_body(p: EvidencePacket) -> str:
    production = p.type == "production"
    sources = {s.id: s for s in p.cited_sources}
    parts: list[str] = []

    stamp = (
        '<div class="stamp production">PRODUCTION COPY</div>'
        if production
        else '<div class="stamp internal">ATTORNEY WORK PRODUCT — PRIVILEGED &amp; '
        "CONFIDENTIAL — INTERNAL USE ONLY</div>"
    )
    scope = (
        "This packet contains report sections and supporting evidence cleared for external "
        "production. Material under privilege designation is withheld in full and listed in "
        "the privilege log. Sections citing claims in unresolved conflicts are excluded and "
        "identified in the manifest. Internal review assessments (confidence, evidence-quality "
        "ratings, reviewer identities, and decision reasoning) are attorney work product and "
        "are not included."
        if production
        else "Prepared for case-team use. This packet reflects the complete current state of "
        "evidence review, including privileged material (marked), blocked sections, and the "
        "full decision log with reviewer reasoning. Do not produce or distribute outside the "
        "case team."
    )

    generated_line = (
        f"Generated {e(_fmt(p.generated_at))} &middot; Packet {e(p.packet_id)} &middot; "
        "Atlas Argus (server-generated)"
        if production
        else f"Generated {e(_fmt(p.generated_at))} by {e(p.generated_by_name)}, "
        f"{e(p.generated_by_role)} &middot; Packet {e(p.packet_id)} &middot; "
        "Atlas Argus (server-generated)"
    )
    parts.append(f"""<header>
{stamp}
<h1>Evidence Packet</h1>
<table class="caption">
<tr><th>Case</th><td>{e(p.case.name)}</td></tr>
<tr><th>Docket</th><td>{e(p.case.docket_ref)}</td></tr>
<tr><th>Aircraft</th><td>{e(p.case.aircraft)}</td></tr>
<tr><th>Accident date</th><td>{e(p.case.accident_date)}</td></tr>
<tr><th>Location</th><td>{e(p.case.location)}</td></tr>
<tr><th>Matter type</th><td>{e(p.case.matter_type)}</td></tr>
</table>
<p class="genline">{generated_line}</p>
<p class="scope">{scope}</p>
</header>""")

    manifest_rows = "\n".join(
        f"<tr><td>{e(entry.paragraph_ref)}</td><td>{e(entry.title)}</td>"
        f"<td>{e(_label(entry.impact_status))}</td><td>{e(entry.reason)}</td></tr>"
        for entry in p.entries
    )
    parts.append(f"""<section>
<h2>Manifest</h2>
<table class="manifest">
<tr><th>&para;</th><th>Section</th><th>Status</th><th>Disposition</th></tr>
{manifest_rows}
</table>
<p class="stats">{p.stats["included"]} section(s) included &middot; {p.stats["excluded"]} excluded &middot; {p.stats["withheld"]} withheld under privilege.</p>
</section>""")

    rendered = [entry for entry in p.entries if entry.disposition == "included"]
    section_parts: list[str] = []
    for entry in rendered:
        marks: list[str] = []
        if not production and entry.impact_status == "privileged_material":
            marks.append('<p class="mark privileged">PRIVILEGED — DO NOT PRODUCE</p>')
        if not production and entry.impact_status == "blocked":
            marks.append(
                f'<p class="mark blocked">BLOCKED FOR REPORT USE — {e(entry.impact_note)}</p>'
            )
        disclosures = "\n".join(_render_disclosure(d, sources) for d in entry.disclosures)
        section_parts.append(f"""<article class="report-section">
<h3>{e(entry.title)} <span class="pref">&para; {e(entry.paragraph_ref)}</span></h3>
{chr(10).join(marks)}
<p class="body-text">{e(entry.text)}</p>
{disclosures}
</article>""")
    empty = (
        '<p class="empty">No report sections are currently eligible for this packet type. '
        "The manifest above identifies what is excluded and why.</p>"
        if not rendered
        else ""
    )
    parts.append(
        f"<section>\n<h2>Report sections</h2>\n{chr(10).join(section_parts)}\n{empty}</section>"
    )

    claim_parts: list[str] = []
    for pc in p.cited_claims:
        if pc.redacted:
            claim_parts.append(
                '<article class="appendix-claim"><h4>Withheld claim</h4>'
                f'<p class="redacted">{e(REDACTED_TEXT)}</p></article>'
            )
            continue
        internal_meta = (
            ""
            if production
            else f'<p class="cite">Status: {e(pc.claim.status)} &middot; report eligibility: '
            f"{e(_label(pc.claim.report_eligibility))} &middot; confidence: "
            f"{round(pc.claim.confidence * 100)}% &middot; evidence quality: "
            f"{e(pc.claim.evidence_quality)} &middot; reviewer: {e(pc.claim.reviewer or '—')}</p>"
        )
        privileged_mark = (
            f'<p class="mark privileged">PRIVILEGED ({e(_label(pc.claim.privilege_status))}) '
            "— DO NOT PRODUCE</p>"
            if not production and _is_withholding(pc.claim)
            else ""
        )
        claim_parts.append(f"""<article class="appendix-claim">
<h4>{e(pc.claim.id)}</h4>
{privileged_mark}
{_render_citation(pc, sources)}
{internal_meta}
</article>""")
    parts.append(
        f"<section>\n<h2>Appendix A — Cited claims</h2>\n{chr(10).join(claim_parts)}\n</section>"
    )

    source_parts: list[str] = []
    for source in p.cited_sources:
        custody = "\n".join(
            f'<li><span class="when">{e(ev["at"])}</span> {e(ev["actor"])} — {e(ev["action"])}</li>'
            for ev in source.custody
        )
        docket = f" &middot; {e(source.docket_ref)}" if source.docket_ref else ""
        source_parts.append(f"""<article class="appendix-source">
<h4>{e(source.title)}</h4>
<p class="cite">{e(_label(source.type))} &middot; {e(source.origin)} &middot; custodian: {e(source.custodian)}{docket}</p>
<p class="cite">SHA-256: <code>{e(source.sha256)}</code> &middot; ingested {e(iso_z(source.ingested_at))}</p>
<ol class="custody">
{custody}
</ol>
</article>""")
    parts.append(
        "<section>\n<h2>Appendix B — Source documents &amp; chain of custody</h2>\n"
        f"{chr(10).join(source_parts)}\n</section>"
    )

    if production and p.privilege_log:
        log_rows = "\n".join(
            f"<tr><td>{e(entry.source.title)}</td><td>{e(_label(entry.source.type))}</td>"
            f"<td>{e(_label(entry.source.privilege_status))}</td><td>{e(entry.source.custodian)}</td>"
            f"<td>&para; {', '.join(e(r) for r in entry.section_refs)}</td></tr>"
            for entry in p.privilege_log
        )
        parts.append(f"""<section>
<h2>Privilege log</h2>
<p class="scope">The following material is withheld in full under privilege designation. Entries identify the withheld document; contents are not described.</p>
<table class="manifest">
<tr><th>Document</th><th>Type</th><th>Designation</th><th>Custodian</th><th>Referenced by</th></tr>
{log_rows}
</table>
</section>""")

    if p.decision_log is not None:
        if not p.decision_log:
            body = '<p class="empty">No review decisions recorded yet.</p>'
        else:
            body = "\n".join(
                f"""<article class="decision">
<h4>{e(_label(d.decision_type))} &middot; <span class="when">{e(iso_z(d.created_at))}</span></h4>
<p class="cite">{e(summary)}</p>
<p class="cite">{e(d.reviewer_name)}, {e(d.reviewer_role)} &middot; {e(_label(d.previous_status))} &rarr; {e(_label(d.new_status))}</p>
<p class="body-text">{e(d.reasoning)}</p>
<p class="cite">Report impact: {e(d.report_impact)}</p>
</article>"""
                for d, summary in p.decision_log
            )
        parts.append(f"<section>\n<h2>Decision log</h2>\n{body}\n</section>")

    return "\n".join(parts)


PACKET_CSS = """
body { margin: 0; background: #f0f0eb; }
.packet { max-width: 800px; margin: 0 auto; padding: 48px 56px; background: #fff; color: #16181d; font: 14px/1.6 Georgia, "Times New Roman", serif; }
h1 { font-size: 26px; letter-spacing: 0.04em; margin: 18px 0 14px; }
h2 { font-size: 17px; border-bottom: 2px solid #16181d; padding-bottom: 4px; margin: 32px 0 14px; }
h3 { font-size: 15px; margin: 20px 0 8px; }
h4 { font-size: 13.5px; margin: 16px 0 6px; }
.pref, .when { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: #5c626e; font-weight: normal; }
.stamp { font: bold 12px/1.4 Arial, sans-serif; letter-spacing: 0.08em; text-align: center; padding: 8px 12px; border: 2px solid; }
.stamp.internal { color: #8a1f1a; border-color: #8a1f1a; }
.stamp.production { color: #1d4f8a; border-color: #1d4f8a; }
.caption { border-collapse: collapse; width: 100%; margin: 10px 0; }
.caption th { text-align: left; font: bold 11px/1.4 Arial, sans-serif; letter-spacing: 0.05em; text-transform: uppercase; color: #5c626e; padding: 3px 14px 3px 0; white-space: nowrap; vertical-align: top; }
.caption td { padding: 3px 0; }
.genline { font: 11.5px/1.5 Arial, sans-serif; color: #5c626e; }
.scope { font-size: 12.5px; color: #3c414b; border-left: 3px solid #c9cdd6; padding-left: 12px; }
.manifest { border-collapse: collapse; width: 100%; font-size: 12px; }
.manifest th, .manifest td { border: 1px solid #c9cdd6; padding: 5px 8px; text-align: left; vertical-align: top; }
.manifest th { font-family: Arial, sans-serif; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; background: #f4f5f7; }
.stats { font: 11.5px Arial, sans-serif; color: #5c626e; }
.body-text { margin: 8px 0; }
.claim-text { font-weight: bold; margin: 6px 0 2px; }
.cite { font: 11.5px/1.5 Arial, sans-serif; color: #5c626e; margin: 2px 0; }
.cite code { font-size: 10.5px; }
blockquote { margin: 6px 0 10px; padding: 6px 12px; border-left: 3px solid #c9cdd6; font-style: italic; color: #3c414b; font-size: 13px; }
.mark { font: bold 11px/1.4 Arial, sans-serif; letter-spacing: 0.06em; padding: 5px 10px; }
.mark.privileged { color: #6a3fae; border: 1px solid #6a3fae; }
.mark.blocked { color: #8a1f1a; border: 1px solid #8a1f1a; }
.redacted { font: italic 13px Georgia, serif; color: #6a3fae; }
.disclosure { border: 1px solid #c9cdd6; background: #fafafa; padding: 10px 14px; margin: 12px 0; }
.disclosure-head { font: bold 12px Arial, sans-serif; letter-spacing: 0.03em; margin: 0 0 6px; }
.disclosure-claim { margin: 8px 0; }
.disclosure-claim .side { font: bold 10.5px Arial, sans-serif; letter-spacing: 0.08em; color: #5c626e; }
.disclosure-note { font: italic 12px Georgia, serif; color: #3c414b; margin: 6px 0 0; }
.custody { font-size: 12px; padding-left: 18px; margin: 4px 0; }
.custody li { margin: 2px 0; }
.appendix-claim, .appendix-source, .decision { margin-bottom: 18px; }
.empty { font-style: italic; color: #5c626e; }
footer { margin-top: 36px; border-top: 1px solid #c9cdd6; padding-top: 10px; }
.integrity { font: 10.5px ui-monospace, Menlo, Consolas, monospace; color: #5c626e; word-break: break-all; }
@page { margin: 18mm; }
@media print { body { background: #fff; } .packet { padding: 0; max-width: none; } }
"""


def render_packet_document(p: EvidencePacket, body: str, body_sha256: str) -> str:
    title = f"Evidence Packet — {p.case.name} ({p.type})"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{e(title)}</title>
<style>{PACKET_CSS}</style>
</head>
<body>
<div class="packet">
{body}
<footer>
<p class="integrity">Integrity: SHA-256 of the packet body (this document's content between header and footer, excluding this line) = {e(body_sha256)}</p>
<p class="integrity">Packet {e(p.packet_id)} &middot; generated {e(_fmt(p.generated_at))} &middot; recorded in the Atlas Argus audit trail.</p>
</footer>
</div>
</body>
</html>"""


def packet_filename(p: EvidencePacket) -> str:
    ts = p.generated_at.replace("-", "").replace(":", "")[:13]
    return f"atlas-argus-{p.type}-packet-{p.case.id}-{ts}.html"


def new_packet_id() -> str:
    return f"pkt-{uuid.uuid4().hex[:12]}"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
