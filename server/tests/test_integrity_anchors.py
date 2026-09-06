from __future__ import annotations

import json

from atlas_argus.integrity_anchors import verify_latest_anchor, write_anchor


def verification(
    *,
    audit_members: list[dict] | None = None,
    section_members: list[dict] | None = None,
) -> dict:
    audit_members = audit_members or [
        {"id": "audit-1", "integrityHash": "audit-1-hash"},
        {"id": "audit-2", "integrityHash": "audit-2-hash"},
    ]
    section_members = section_members or [
        {
            "id": "revision-1",
            "sectionId": "section-1",
            "parentRevisionId": None,
            "contentSha256": "content-1",
            "integrityHash": "section-1-hash",
        }
    ]
    return {
        "audit": {
            "checked": len(audit_members),
            "rootIntegrityHash": audit_members[-1]["integrityHash"] if audit_members else None,
            "_anchorMembers": audit_members,
        },
        "packets": {
            "checked": 1,
            "rootIntegrityHash": "packet-root",
            "_anchorMembers": [{"id": "packet-1", "integrityHash": "packet-root"}],
        },
        "sections": {
            "checked": len(section_members),
            "rootIntegrityHash": "section-root-" + str(len(section_members)),
            "_anchorMembers": section_members,
        },
        "decisions": {
            "checked": 1,
            "rootIntegrityHash": "decision-root",
            "_anchorMembers": [{"id": "decision-1", "integrityHash": "decision-root"}],
        },
        "extractions": {
            "runs": 1,
            "pages": 2,
            "documents": 1,
            "rootIntegrityHash": "extraction-root",
            "_anchorMembers": [
                {"kind": "document", "id": "source-1", "integrityHash": "doc-hash"},
                {"kind": "run", "id": "run-1", "integrityHash": "run-hash"},
                {"kind": "page", "id": "page-1", "integrityHash": "page-1-hash"},
                {"kind": "page", "id": "page-2", "integrityHash": "page-2-hash"},
            ],
        },
    }


def configure(monkeypatch, tmp_path):
    anchor_dir = tmp_path / "anchors"
    anchor_dir.mkdir()
    key_file = tmp_path / "anchor.key"
    key_file.write_bytes(b"external-test-key-material-at-least-32-bytes")
    key_file.chmod(0o600)
    monkeypatch.setenv("ATLAS_ARGUS_INTEGRITY_ANCHOR_DIR", str(anchor_dir))
    monkeypatch.setenv("ATLAS_ARGUS_INTEGRITY_ANCHOR_KEY_FILE", str(key_file))


def test_external_anchor_matches_and_detects_tail_deletion(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    write_anchor("case-1", verification())

    assert verify_latest_anchor("case-1", verification())["status"] == "matched"
    rolled_back = verify_latest_anchor("case-1", verification(audit_members=[
        {"id": "audit-1", "integrityHash": "audit-1-hash"}
    ]))
    assert rolled_back["ok"] is False
    assert rolled_back["status"] == "rollback_detected"
    assert rolled_back["chains"] == ["audit"]


def test_external_anchor_signature_tampering_is_rejected(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    path = write_anchor("case-1", verification())
    document = json.loads(path.read_text())
    document["payload"]["checkpoint"]["audit"]["count"] = 0
    path.write_text(json.dumps(document))

    result = verify_latest_anchor("case-1", verification())
    assert result["ok"] is False
    assert result["status"] == "invalid"


def test_external_anchor_proves_ordered_prefix_before_accepting_append(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    original = verification()
    write_anchor("case-1", original)

    valid_append = verification(
        audit_members=[
            *original["audit"]["_anchorMembers"],
            {"id": "audit-3", "integrityHash": "audit-3-hash"},
        ]
    )
    assert verify_latest_anchor("case-1", valid_append)["status"] == "advanced"

    replaced_prefix = verification(
        audit_members=[
            {"id": "audit-1", "integrityHash": "rewritten-1"},
            {"id": "audit-2", "integrityHash": "rewritten-2"},
            {"id": "audit-3", "integrityHash": "rewritten-3"},
        ]
    )
    result = verify_latest_anchor("case-1", replaced_prefix)
    assert result["ok"] is False
    assert result["status"] == "checkpoint_mismatch"
    assert result["chains"] == ["audit"]


def test_external_anchor_proves_aggregate_members_survive_extension(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    original = verification()
    write_anchor("case-1", original)
    replacement = verification(
        section_members=[
            {
                **original["sections"]["_anchorMembers"][0],
                "integrityHash": "rewritten-section-hash",
            },
            {
                "id": "revision-2",
                "sectionId": "section-1",
                "parentRevisionId": "revision-1",
                "contentSha256": "content-2",
                "integrityHash": "section-2-hash",
            },
        ]
    )
    result = verify_latest_anchor("case-1", replacement)
    assert result["ok"] is False
    assert result["status"] == "checkpoint_mismatch"
    assert result["chains"] == ["sections"]
