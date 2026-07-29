#!/usr/bin/env python3
"""Authenticated packet-generation smoke test.

This is intentionally opt-in from ``scripts/smoke.sh`` because it creates a
packet artifact. For local/demo rehearsal it can enroll MFA automatically from
the returned seed secret; for production, provide a current MFA code instead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


class SmokeFailure(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode()
        request = Request(
            f"{self.base_url}/api{path}",
            data=payload,
            method=method,
            headers={"content-type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read().decode()
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise SmokeFailure(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"{method} {path} returned non-JSON response") from exc


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SmokeFailure(f"{name} is required for packet smoke")
    return value


def totp(secret: str, *, for_time: int | None = None) -> str:
    clean = secret.replace(" ", "").upper()
    clean += "=" * ((8 - len(clean) % 8) % 8)
    key = base64.b32decode(clean)
    counter = int((for_time or int(time.time())) / 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def verify_or_enable_mfa(client: Client, status: dict[str, Any]) -> None:
    if status.get("verified") is True:
        return
    if status.get("enabled") is True:
        code = os.environ.get("ATLAS_ARGUS_SMOKE_MFA_CODE", "")
        if code:
            verified = client.request("POST", "/auth/mfa/verify", {"code": code})
            if verified.get("verified") is not True:
                raise SmokeFailure("MFA verification did not mark the session verified")
            return
        if os.environ.get("ATLAS_ARGUS_SMOKE_ALLOW_MFA_ENROLL") != "1":
            raise SmokeFailure("ATLAS_ARGUS_SMOKE_MFA_CODE is required for packet smoke")
    if os.environ.get("ATLAS_ARGUS_SMOKE_ALLOW_MFA_ENROLL") != "1":
        raise SmokeFailure(
            "MFA is not enabled for the smoke reviewer. Set "
            "ATLAS_ARGUS_SMOKE_ALLOW_MFA_ENROLL=1 for local/demo rehearsal, "
            "or pre-enroll MFA and provide ATLAS_ARGUS_SMOKE_MFA_CODE."
        )
    enrollment = client.request("POST", "/auth/mfa/enroll", {})
    secret = enrollment.get("secret")
    if not isinstance(secret, str) or not secret:
        raise SmokeFailure("MFA enrollment did not return a secret")
    enabled = client.request("POST", "/auth/mfa/enable", {"code": totp(secret)})
    if enabled.get("verified") is not True:
        raise SmokeFailure("MFA enablement did not mark the session verified")


def choose_case_id(client: Client) -> str:
    explicit = os.environ.get("ATLAS_ARGUS_SMOKE_CASE_ID")
    if explicit:
        return explicit
    cases = client.request("GET", "/cases").get("cases")
    if not isinstance(cases, list) or not cases:
        raise SmokeFailure("No case memberships were returned for smoke reviewer")
    case_file = cases[0].get("caseFile") if isinstance(cases[0], dict) else None
    case_id = case_file.get("id") if isinstance(case_file, dict) else None
    if not isinstance(case_id, str) or not case_id:
        raise SmokeFailure("First case membership did not include a case id")
    return case_id


def main() -> int:
    base_url = os.environ.get("ATLAS_ARGUS_SMOKE_BASE_URL") or os.environ.get(
        "ATLAS_ARGUS_BASE_URL", "http://localhost:8100"
    )
    username = env("ATLAS_ARGUS_SMOKE_USERNAME")
    password = env("ATLAS_ARGUS_SMOKE_PASSWORD")
    packet_type = os.environ.get("ATLAS_ARGUS_SMOKE_PACKET_TYPE", "production")

    client = Client(base_url)
    session = client.request("POST", "/auth/login", {"username": username, "password": password})
    if session.get("mustChangePassword") is True:
        raise SmokeFailure("Smoke reviewer must change password before packet smoke can run")
    verify_or_enable_mfa(client, session.get("mfa", {}))

    case_id = choose_case_id(client)
    encoded_case_id = quote(case_id, safe="")
    generated = client.request(
        "POST",
        f"/cases/{encoded_case_id}/packets",
        {"packetType": packet_type},
    )
    packet_id = generated.get("packetId")
    if not isinstance(packet_id, str) or not packet_id:
        raise SmokeFailure("Packet generation did not return packetId")
    if generated.get("packetIntegrityHash") in (None, ""):
        raise SmokeFailure("Packet generation did not return packetIntegrityHash")

    query = urlencode({"limit": 25, "offset": 0})
    listing = client.request("GET", f"/cases/{encoded_case_id}/packets?{query}")
    packets = listing.get("packets")
    if listing.get("verification", {}).get("ok") is not True:
        raise SmokeFailure("Packet list integrity verification failed")
    if not isinstance(packets, list) or not any(p.get("packetId") == packet_id for p in packets):
        raise SmokeFailure("Generated packet was not present in packet list")

    encoded_packet_id = quote(packet_id, safe="")
    detail = client.request("GET", f"/cases/{encoded_case_id}/packets/{encoded_packet_id}")
    if detail.get("verification", {}).get("ok") is not True:
        raise SmokeFailure("Packet detail integrity verification failed")
    if detail.get("document") != generated.get("document"):
        raise SmokeFailure("Retrieved packet document does not match generated document")
    document = detail.get("document")
    if not isinstance(document, str):
        raise SmokeFailure("Packet detail did not include document text")
    document_sha = hashlib.sha256(document.encode()).hexdigest()
    if detail.get("documentSha256") != document_sha:
        raise SmokeFailure("Retrieved packet documentSha256 does not match document")

    verification = client.request(
        "GET",
        f"/cases/{encoded_case_id}/packets/{encoded_packet_id}/verify",
    )
    if verification.get("ok") is not True:
        raise SmokeFailure("Dedicated packet verification endpoint failed")

    print(
        "packet smoke ok: "
        f"case={case_id} packet={packet_id} type={packet_type} sha256={document_sha}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"packet smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
