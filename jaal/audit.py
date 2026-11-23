"""Tamper-evident audit trail + role-based access control.

Every API call is appended to a JSON-lines log in which each entry carries the SHA-256
hash of the previous entry (a hash chain). Editing, deleting or re-ordering any past
entry breaks every hash after it, which `verify()` detects and pinpoints.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
from pathlib import Path

GENESIS = "0" * 64

ROLES = {
    "viewer": {"graph", "targets", "alerts", "cases", "summary"},
    "analyst": {"graph", "targets", "alerts", "cases", "summary", "entity", "doc", "path", "ingest", "er_read",
                "links", "timeline", "search"},
    "supervisor": {"graph", "targets", "alerts", "cases", "summary", "entity", "doc", "path", "ingest", "er_read",
                   "links", "timeline", "search", "er_decide", "audit", "evaluate"},
    "auditor": {"summary", "audit"},
}
ROLE_DESC = {
    "viewer": "Field officer — network overview only; phone, account and vehicle identifiers are masked.",
    "analyst": "Investigating analyst — full dossiers, source documents, path queries, FIR ingestion.",
    "supervisor": "Supervisory officer — everything an analyst can do + entity-resolution decisions + audit log.",
    "auditor": "Independent auditor — audit trail and chain verification only; no case data.",
}
# Demo identities (token -> user). In production these would come from the agency's SSO.
USERS = {
    "tok-viewer": {"user": "const.patil", "name": "Const. S. Patil", "role": "viewer"},
    "tok-analyst": {"user": "si.sharma", "name": "SI R. Sharma", "role": "analyst"},
    "tok-supervisor": {"user": "dcp.rao", "name": "DCP A. Rao", "role": "supervisor"},
    "tok-auditor": {"user": "audit.iyer", "name": "Auditor K. Iyer", "role": "auditor"},
}


def _hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.path.touch(exist_ok=True)

    def _last(self):
        last = None
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last) if last else None

    def append(self, user: dict, action: str, target: str = "", outcome: str = "ok", detail: dict | None = None):
        with self.lock:
            prev = self._last()
            entry = {"seq": (prev["seq"] + 1) if prev else 1,
                     "ts": dt.datetime.now().isoformat(timespec="seconds"),
                     "user": user.get("user", "anonymous"), "role": user.get("role", "-"),
                     "action": action, "target": target, "outcome": outcome, "detail": detail or {},
                     "prev": prev["hash"] if prev else GENESIS}
            entry["hash"] = _hash(entry)
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            return entry

    def entries(self, limit: int = 200):
        with open(self.path) as f:
            rows = [json.loads(l) for l in f if l.strip()]
        return rows[-limit:][::-1]

    def verify(self):
        prev, n = GENESIS, 0
        with open(self.path) as f:
            for i, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                e = json.loads(line)
                n += 1
                if e.get("prev") != prev:
                    return {"ok": False, "entries": n, "broken_at": e.get("seq", i),
                            "reason": "previous-hash link does not match (entry removed or re-ordered)"}
                if _hash(e) != e.get("hash"):
                    return {"ok": False, "entries": n, "broken_at": e.get("seq", i),
                            "reason": "entry content was modified after it was written"}
                prev = e["hash"]
        return {"ok": True, "entries": n, "head": prev}


# ------------------------------------------------------------------ PII masking for low-privilege roles
_PHONE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
_ACCT = re.compile(r"(?<![\d…])(\d{11,16})(?!\d)")
_VEH = re.compile(r"\b([A-Z]{2})(\d{2})([A-Z]{1,3})(\d{4})\b")


def mask_text(s: str) -> str:
    s = _PHONE.sub(lambda m: m.group(1)[:2] + "XXXXXX" + m.group(1)[-2:], s)
    s = _ACCT.sub(lambda m: "X" * (len(m.group(1)) - 4) + m.group(1)[-4:], s)
    s = _VEH.sub(lambda m: m.group(1) + m.group(2) + "XX" + "XX" + m.group(4)[-2:], s)
    return s


_IDENT = re.compile(r"^(PH|AC|VH|U|IMEI):")


def mask(obj):
    """Recursively mask identifiers. Node ids that embed an identifier become opaque but stable tokens,
    so the masked graph still renders and links correctly."""
    if isinstance(obj, str):
        if _IDENT.match(obj):
            return obj.split(":")[0] + ":#" + hashlib.sha1(obj.encode()).hexdigest()[:10]
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask(v) for k, v in obj.items()}
    return obj
