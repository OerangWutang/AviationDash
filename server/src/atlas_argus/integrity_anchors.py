"""Signed checkpoints stored outside the mutable application database."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import integrity_anchor_dir, integrity_anchor_key_file
from .integrity import canonical_json

_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_CHAIN_MODES = {
    "audit": "ordered_prefix",
    "packets": "ordered_prefix",
    "sections": "set_extension",
    "decisions": "ordered_prefix",
    "extractions": "set_extension",
}


def checkpoint_from_verification(verification: dict) -> dict:
    extractions = verification["extractions"]
    counts = {
        "audit": verification["audit"]["checked"],
        "packets": verification["packets"]["checked"],
        "sections": verification["sections"]["checked"],
        "decisions": verification["decisions"]["checked"],
        "extractions": (
            extractions["runs"] + extractions["pages"] + extractions["documents"]
        ),
    }
    roots = {
        name: verification[name]["rootIntegrityHash"]
        for name in ("audit", "packets", "sections", "decisions", "extractions")
    }
    checkpoint: dict[str, dict] = {}
    for name, mode in _CHAIN_MODES.items():
        members = verification[name].get("_anchorMembers")
        if not isinstance(members, list) or len(members) != counts[name]:
            raise RuntimeError(
                f"Integrity verification did not provide a complete {name} member proof."
            )
        checkpoint[name] = {
            "count": counts[name],
            "root": roots[name],
            "mode": mode,
            "members": members,
        }
    return checkpoint


def _paths(case_id: str) -> tuple[Path, Path]:
    if not _SAFE_CASE_ID.fullmatch(case_id):
        raise RuntimeError("Matter id is unsafe for external anchor storage.")
    directory = integrity_anchor_dir()
    key_file = integrity_anchor_key_file()
    if directory is None or key_file is None:
        raise RuntimeError("External integrity anchor storage is not configured.")
    return directory / case_id, key_file


def _key(key_file: Path) -> bytes:
    try:
        key = key_file.read_bytes().strip()
    except OSError as exc:
        raise RuntimeError("External integrity anchor key is unreadable.") from exc
    if len(key) < 32:
        raise RuntimeError("External integrity anchor key is too short.")
    return key


def _signature(payload: dict, key: bytes) -> str:
    return hmac.new(
        key,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def write_anchor(case_id: str, verification: dict) -> Path:
    """Append one signed checkpoint; existing checkpoint files are untouched."""
    case_directory, key_file = _paths(case_id)
    case_directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    payload = {
        "schema": "atlas_argus.external_integrity_anchor.v2",
        "caseId": case_id,
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "checkpoint": checkpoint_from_verification(verification),
    }
    document = {
        "payload": payload,
        "signature": _signature(payload, _key(key_file)),
    }
    filename = f"{now:%Y%m%dT%H%M%S.%fZ}-{uuid.uuid4().hex}.json"
    destination = case_directory / filename
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(document))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    directory_descriptor = os.open(case_directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return destination


def verify_latest_anchor(case_id: str, verification: dict) -> dict:
    try:
        case_directory, key_file = _paths(case_id)
    except RuntimeError as exc:
        return {"ok": False, "status": "not_configured", "detail": str(exc)}
    candidates = sorted(case_directory.glob("*.json")) if case_directory.is_dir() else []
    if not candidates:
        return {
            "ok": False,
            "status": "missing",
            "detail": "No external checkpoint exists for this matter.",
        }
    latest = candidates[-1]
    try:
        document = json.loads(latest.read_text(encoding="utf-8"))
        payload = document["payload"]
        supplied_signature = document["signature"]
        expected_signature = _signature(payload, _key(key_file))
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature mismatch")
        if (
            payload.get("schema") != "atlas_argus.external_integrity_anchor.v2"
            or payload.get("caseId") != case_id
            or not isinstance(payload.get("checkpoint"), dict)
        ):
            raise ValueError("invalid payload")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": "invalid",
            "anchor": latest.name,
            "detail": f"External checkpoint is invalid: {exc}",
        }

    current = checkpoint_from_verification(verification)
    anchored = payload["checkpoint"]
    rollback: list[str] = []
    mismatched: list[str] = []
    advanced: list[str] = []
    for chain_name, current_chain in current.items():
        anchored_chain = anchored.get(chain_name)
        if not isinstance(anchored_chain, dict):
            mismatched.append(chain_name)
            continue
        anchored_count = anchored_chain.get("count")
        anchored_members = anchored_chain.get("members")
        mode = anchored_chain.get("mode")
        current_members = current_chain.get("members")
        if (
            not isinstance(anchored_count, int)
            or not isinstance(anchored_members, list)
            or len(anchored_members) != anchored_count
            or not isinstance(current_members, list)
            or mode != _CHAIN_MODES[chain_name]
        ):
            mismatched.append(chain_name)
            continue
        if current_chain["count"] < anchored_count:
            rollback.append(chain_name)
            continue

        if mode == "ordered_prefix":
            prefix_matches = current_members[:anchored_count] == anchored_members
        else:
            current_by_member = {
                (member.get("kind"), member.get("id")): member
                for member in current_members
                if isinstance(member, dict)
            }
            prefix_matches = all(
                isinstance(member, dict)
                and current_by_member.get((member.get("kind"), member.get("id"))) == member
                for member in anchored_members
            )
            # Section members predate the generic ``kind`` discriminator; their
            # revision id is independently unique and is the set-extension key.
            if chain_name == "sections":
                current_by_id = {
                    member.get("id"): member
                    for member in current_members
                    if isinstance(member, dict)
                }
                prefix_matches = all(
                    isinstance(member, dict)
                    and current_by_id.get(member.get("id")) == member
                    for member in anchored_members
                )

        if not prefix_matches:
            mismatched.append(chain_name)
        elif current_chain["count"] == anchored_count:
            if current_chain["root"] != anchored_chain.get("root"):
                mismatched.append(chain_name)
        else:
            advanced.append(chain_name)

    if rollback:
        return {
            "ok": False,
            "status": "rollback_detected",
            "anchor": latest.name,
            "createdAt": payload["createdAt"],
            "chains": rollback,
        }
    if mismatched:
        return {
            "ok": False,
            "status": "checkpoint_mismatch",
            "anchor": latest.name,
            "createdAt": payload["createdAt"],
            "chains": mismatched,
        }
    return {
        "ok": True,
        "status": "advanced" if advanced else "matched",
        "anchor": latest.name,
        "createdAt": payload["createdAt"],
        "advancedChains": advanced,
    }


__all__ = [
    "checkpoint_from_verification",
    "verify_latest_anchor",
    "write_anchor",
]
