"""Multi-source ingestion.

Every parser turns a raw source into *records* that carry provenance
(doc id + line number or character span), so any downstream claim can be
traced back to the exact place it came from.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------------- normalisers
def norm_phone(s: str) -> str | None:
    d = re.sub(r"\D", "", s or "")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "6789" else None


def norm_vehicle(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


@dataclass
class Evidence:
    doc: str           # document id
    loc: str           # "L123" (line) or "C10-45" (char span)
    text: str          # short snippet shown to the analyst

    def to_dict(self):
        return {"doc": self.doc, "loc": self.loc, "text": self.text[:220]}


@dataclass
class PersonRecord:
    rid: str                       # stable record id, e.g. "kyc:1234"
    source: str                    # fir | subscriber | kyc | kycsig | rto | social
    name: str
    aliases: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    dob: str = ""
    age: int | None = None
    id_type: str = ""
    id_number: str = ""
    city: str = ""
    evidence: Evidence | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Dataset:
    docs: dict = field(default_factory=dict)          # doc_id -> {title, kind, lines|text, sha256}
    persons: list = field(default_factory=list)       # PersonRecord
    calls: list = field(default_factory=list)
    txns: list = field(default_factory=list)
    accounts: dict = field(default_factory=dict)      # acct -> kyc row (+ evidence)
    vehicles: dict = field(default_factory=dict)      # reg -> rto row
    anpr: list = field(default_factory=list)
    handles: dict = field(default_factory=dict)       # handle -> account dict
    messages: list = field(default_factory=list)
    cases: dict = field(default_factory=dict)         # case_no -> parsed FIR
    subscriber_of: dict = field(default_factory=dict)  # phone -> rid


# --------------------------------------------------------------------- file helpers
def _register_doc(ds: Dataset, doc_id: str, path: Path, kind: str, title: str):
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    ds.docs[doc_id] = {"id": doc_id, "title": title, "kind": kind, "path": path.name,
                       "sha256": hashlib.sha256(raw).hexdigest(), "lines": text.splitlines(),
                       "bytes": len(raw)}
    return text


def _csv(ds: Dataset, root: Path, name: str, kind: str, title: str):
    p = root / name
    if not p.exists():
        return []
    _register_doc(ds, name, p, kind, title)
    with open(p, newline="") as f:
        return [(i + 2, row) for i, row in enumerate(csv.DictReader(f))]  # line numbers incl. header


def _snip(ds, doc, line):
    return ds.docs[doc]["lines"][line - 1]


# --------------------------------------------------------------------- FIR parser
PHONE_RE = re.compile(r"(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}\b")
ACCT_RE = re.compile(r"\b(?:a/c|account)(?:\s+no\.?)?\s*:?\s*(\d{9,18})", re.I)
VEH_RE = re.compile(r"\b([A-Z]{2})\s?(\d{1,2})\s?([A-Z]{1,3})\s?(\d{4})\b")
AMT_RE = re.compile(r"Rs\.?\s*([\d,]+)")
MO_LEX = {
    "kyc": "KYC-update pretext", "otp": "OTP harvesting", "sim": "SIM supply", "below rs. 50,000": "structuring",
    "45,000": "structuring", "renting": "account renting (mule)", "ganja": "narcotics", "ndps": "narcotics",
    "consignment": "trafficking consignment", "concealed": "concealment", "transport": "transport front",
    "motorcycle": "vehicle theft", "assault": "assault", "seth": "unknown handler 'Seth'", "kolkata side": "Kolkata money link",
    "pmla": "money laundering", "round amounts": "structuring", "customer lists": "victim data lists",
}


def parse_fir(ds: Dataset, doc_id: str, text: str):
    def field_(label):
        m = re.search(label + r"\s*:\s*(.+?)(?:\s{3,}|\n|$)", text)
        return m.group(1).strip() if m else ""

    case_no = field_("FIR No") or doc_id
    case = dict(case_no=case_no, doc=doc_id, station=field_("Police Station"), state=field_("State"),
                date=field_("Date of Report"), occurrence=field_("Date of Occurrence"), sections=field_("Sections"),
                persons=[], phones=[], accounts=[], vehicles=[], amounts=[], mo=[])
    offset = 0
    for ln, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        role = None
        m = re.match(r"\s*(?:\d+\.\s*)(.+)", line)
        if low.startswith("complainant:"):
            role, body = "complainant", line.split(":", 1)[1]
        elif m and re.search(r"\b(arrested|absconding|bailed|notice issued)\b", low):
            role, body = "accused", m.group(1)
        if role:
            name_part = re.split(r",", body)[0].strip()
            names = [x.strip() for x in re.split(r"\s+@\s+|\s+alias\s+", name_part)]
            name, aliases = names[0], names[1:]
            if re.match(r"^(SI|PSI|Inspector|Branch Manager)\b", name):
                name = ""
            age = re.search(r"aged\s+(?:about\s+)?(\d{2})", body)
            city = re.search(r"r/o\s+([^,]+),\s*([A-Za-z ]+?)(?:,|$)", body)
            phones = [p for p in (norm_phone(x) for x in PHONE_RE.findall(body)) if p]
            status = re.search(r"\((arrested|absconding|bailed|notice issued)\)", body)
            if name:
                rec = PersonRecord(rid=f"fir:{case_no}:{name}", source="fir", name=name, aliases=aliases,
                                   phones=phones, age=int(age.group(1)) if age else None,
                                   city=(city.group(2).strip() if city else ""),
                                   evidence=Evidence(doc_id, f"L{ln}", line.strip()),
                                   meta={"case": case_no, "role": role, "status": status.group(1) if status else ""})
                ds.persons.append(rec)
                case["persons"].append({"rid": rec.rid, "role": role, "status": rec.meta["status"], "line": ln})
        for p in PHONE_RE.findall(line):
            n = norm_phone(p)
            if n:
                case["phones"].append({"phone": n, "line": ln, "text": line.strip(), "context_role": role})
        for a in ACCT_RE.findall(line):
            case["accounts"].append({"account": a, "line": ln, "text": line.strip()})
        for v in VEH_RE.findall(line):
            case["vehicles"].append({"reg": "".join(v), "line": ln, "text": line.strip()})
        for a in AMT_RE.findall(line):
            case["amounts"].append(int(a.replace(",", "")))
        for k, tag in MO_LEX.items():
            if k in low and tag not in case["mo"]:
                case["mo"].append(tag)
        offset += len(line) + 1
    # de-dup artefacts
    for key, f in (("phones", "phone"), ("accounts", "account"), ("vehicles", "reg")):
        seen, out = set(), []
        for x in case[key]:
            if x[f] not in seen:
                seen.add(x[f])
                out.append(x)
        case[key] = out
    case["text"] = text
    ds.cases[case_no] = case
    return case


# --------------------------------------------------------------------- loader
def load(root: str | Path) -> Dataset:
    root = Path(root)
    ds = Dataset()

    # FIRs (unstructured text)
    for p in sorted((root / "firs").glob("*.txt")):
        doc_id = "firs/" + p.name
        text = _register_doc(ds, doc_id, p, "fir", p.stem.replace("_", "/").replace("FIR/", "FIR "))
        parse_fir(ds, doc_id, text)

    # Telecom subscriber registry
    for ln, row in _csv(ds, root, "subscribers.csv", "telecom", "Telecom subscriber registry"):
        ph = norm_phone(row["msisdn"])
        if not ph:
            continue
        rid = f"sub:{ph}"
        ds.subscriber_of[ph] = rid
        ds.persons.append(PersonRecord(rid=rid, source="subscriber", name=row["subscriber_name"], phones=[ph],
                                       id_type=row["id_type"], id_number=row["id_number"], city=row["address_city"],
                                       evidence=Evidence("subscribers.csv", f"L{ln}", _snip(ds, "subscribers.csv", ln)),
                                       meta={"activation": row["activation_date"]}))

    # CDR
    for ln, row in _csv(ds, root, "cdr.csv", "telecom", "Call Detail Records"):
        a, b = norm_phone(row["caller"]), norm_phone(row["callee"])
        if a and b:
            ds.calls.append(dict(id=row["call_id"], ts=row["timestamp"], a=a, b=b, dur=int(row["duration_s"] or 0),
                                 imei=row["caller_imei"], cell=row["cell_id"], line=ln))

    # Bank KYC
    for ln, row in _csv(ds, root, "bank_kyc.csv", "financial", "Bank account KYC"):
        acct = row["account_no"].strip()
        ev = Evidence("bank_kyc.csv", f"L{ln}", _snip(ds, "bank_kyc.csv", ln))
        ds.accounts[acct] = dict(row, evidence=ev.to_dict(), line=ln)
        ph = norm_phone(row["phone"])
        if row["holder_type"] == "INDIVIDUAL":
            ds.persons.append(PersonRecord(rid=f"kyc:{acct}", source="kyc", name=row["holder_name"],
                                           phones=[ph] if ph else [], dob=row["dob"], id_type="PAN" if row["pan"] else "",
                                           id_number=row["pan"], city=row["city"], evidence=ev, meta={"account": acct}))
        elif row["authorised_signatory"]:
            ds.persons.append(PersonRecord(rid=f"kycsig:{acct}", source="kycsig", name=row["authorised_signatory"],
                                           phones=[ph] if ph else [], city=row["city"], evidence=ev,
                                           meta={"account": acct, "org": row["holder_name"]}))

    for ln, row in _csv(ds, root, "transactions.csv", "financial", "Fund transfers"):
        ds.txns.append(dict(id=row["txn_id"], ts=row["timestamp"], src=row["from_account"], dst=row["to_account"],
                            amount=int(row["amount"]), mode=row["mode"], narration=row["narration"], line=ln))

    # Vehicles
    for ln, row in _csv(ds, root, "rto.csv", "vehicle", "RTO vehicle registry"):
        reg = norm_vehicle(row["reg_no"])
        ev = Evidence("rto.csv", f"L{ln}", _snip(ds, "rto.csv", ln))
        ds.vehicles[reg] = dict(row, evidence=ev.to_dict())
        ph = norm_phone(row["owner_phone"])
        ds.persons.append(PersonRecord(rid=f"rto:{reg}", source="rto", name=row["owner_name"], phones=[ph] if ph else [],
                                       city=row["city"], evidence=ev, meta={"vehicle": reg}))
    for ln, row in _csv(ds, root, "anpr.csv", "vehicle", "ANPR camera hits"):
        ds.anpr.append(dict(id=row["hit_id"], ts=row["timestamp"], reg=norm_vehicle(row["reg_no"]),
                            camera=row["camera"], line=ln))

    # Social media (OSINT)
    sp = root / "social.json"
    if sp.exists():
        _register_doc(ds, "social.json", sp, "osint", "Social media export")
        data = json.loads(sp.read_text())
        lines = ds.docs["social.json"]["lines"]

        def find_line(needle):
            for i, l in enumerate(lines, start=1):
                if needle in l:
                    return i
            return 1

        for acc in data["accounts"]:
            ds.handles[acc["handle"]] = acc
            phs = [p for p in (norm_phone(x) for x in PHONE_RE.findall(acc.get("bio", ""))) if p]
            ln = find_line(f'"handle": "{acc["handle"]}"')
            ds.persons.append(PersonRecord(rid=f"soc:{acc['handle']}", source="social", name=acc["display_name"],
                                           aliases=[], phones=phs, city=acc.get("city", ""),
                                           evidence=Evidence("social.json", f"L{ln}",
                                                             f'{acc["handle"]} "{acc["display_name"]}" bio: {acc.get("bio","")}'),
                                           meta={"handle": acc["handle"]}))
        for m in data["messages"]:
            m = dict(m, line=find_line(f'"id": "{m["id"]}"'))
            ds.messages.append(m)
    return ds


def manifest(ds: Dataset) -> list[dict]:
    """Chain-of-custody manifest: every ingested file with its SHA-256."""
    return [{"doc": d["id"], "title": d["title"], "kind": d["kind"], "sha256": d["sha256"], "bytes": d["bytes"],
             "lines": len(d["lines"])} for d in ds.docs.values()]
