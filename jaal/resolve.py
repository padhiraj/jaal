"""Entity resolution: decide which records describe the same real person.

Pairwise evidence scoring (all components are explained, never a black box):
    shared government ID (PAN / Aadhaar)  +1.00
    shared phone number                   +0.45
    same date of birth                    +0.35   (conflicting DOB -0.50)
    age consistent with DOB / other age   +0.10   (conflict -0.40)
    name similarity (Indic-aware)         +0.60 x similarity
    same city                             +0.10

    score >= 0.95  -> auto-merge (union-find)
    0.65 - 0.95    -> sent to the human review queue
Supervisors can accept / reject a queued pair; decisions are replayed on every rebuild.
"""
from __future__ import annotations

import itertools
import re

from .ingest import PersonRecord

HONORIFICS = {"mr", "mrs", "ms", "shri", "smt", "sri", "dr", "md", "mohd", "mohammed", "mohammad", "late", "kumari"}
TRANSLIT = [("aa", "a"), ("ee", "i"), ("oo", "u"), ("ai", "e"), ("ei", "e"), ("ou", "u"), ("ph", "f"), ("w", "v"),
            ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("sh", "s"), ("z", "j"), ("q", "k"),
            ("ck", "k"), ("y", "i")]
MERGE_T, REVIEW_T = 0.95, 0.65


def canon(name: str) -> list[str]:
    """Lower-case, strip honorifics/punctuation and fold common transliteration variants."""
    s = re.sub(r"[^a-z\s.]", " ", (name or "").lower()).replace(".", " ")
    toks = [t for t in s.split() if t not in HONORIFICS]
    out = []
    for t in toks:
        for a, b in TRANSLIT:
            t = t.replace(a, b)
        t = re.sub(r"(.)\1+", r"\1", t)  # collapse doubled letters
        if len(t) > 3 and t.endswith("e"):
            t = t[:-1]
        out.append(t)
    return out


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    rng = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    m = 0
    for i, c in enumerate(a):
        for j in range(max(0, i - rng), min(lb, i + rng + 1)):
            if not mb[j] and b[j] == c:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    j = (m / la + m / lb + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return j + p * 0.1 * (1 - j)


def _tok_sim(x: str, y: str) -> float:
    if len(x) == 1 or len(y) == 1:
        return 0.9 if x[0] == y[0] else 0.0
    return jaro_winkler(x, y)


def name_similarity(n1: str, n2: str) -> float:
    a, b = canon(n1), canon(n2)
    if not a or not b:
        return 0.0
    if len(a) == 1 or len(b) == 1:  # nickname / single token: compare against every token
        return max(_tok_sim(x, y) for x in a for y in b) if (len(a) == 1 and len(b) == 1) else \
            max(_tok_sim(x, y) for x in a for y in b) * 0.75
    first = _tok_sim(a[0], b[0])
    last = _tok_sim(a[-1], b[-1])
    return 0.4 * first + 0.6 * last


def _names(r: PersonRecord):
    return [r.name] + list(r.aliases)


def _birth_year(r: PersonRecord):
    if r.dob:
        return int(r.dob[:4])
    if r.age:
        return 2026 - r.age
    return None


def score_pair(a: PersonRecord, b: PersonRecord) -> tuple[float, list[str]]:
    s, why = 0.0, []
    if a.id_number and a.id_number == b.id_number and "XXXX" not in a.id_number:
        s += 1.0
        why.append(f"same {a.id_type or 'ID'} {a.id_number} (+1.00)")
    shared = set(a.phones) & set(b.phones)
    if shared:
        s += 0.45
        why.append(f"shared phone {', '.join(sorted(shared))} (+0.45)")
    if a.dob and b.dob:
        if a.dob == b.dob:
            s += 0.35
            why.append(f"same DOB {a.dob} (+0.35)")
        else:
            s -= 0.5
            why.append(f"DOB conflict {a.dob} vs {b.dob} (-0.50)")
    ya, yb = _birth_year(a), _birth_year(b)
    if ya and yb and not (a.dob and b.dob):
        if abs(ya - yb) <= 2:
            s += 0.1
            why.append("age consistent (+0.10)")
        elif abs(ya - yb) > 5:
            s -= 0.4
            why.append(f"age conflict (~{ya} vs ~{yb}) (-0.40)")
    best, pair = 0.0, ("", "")
    for x in _names(a):
        for y in _names(b):
            v = name_similarity(x, y)
            if v > best:
                best, pair = v, (x, y)
    if best:
        s += 0.6 * best
        why.append(f"name '{pair[0]}' ~ '{pair[1]}' sim {best:.2f} (+{0.6 * best:.2f})")
    if a.city and b.city and a.city.lower() == b.city.lower():
        s += 0.1
        why.append(f"same city {a.city} (+0.10)")
    return round(s, 3), why


class UnionFind:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def resolve(records: list[PersonRecord], decisions: dict | None = None):
    """Returns (entities, merges, review_queue).

    entities: list of dicts {id, name, records:[rid], aliases, phones, ...}
    decisions: {"rid1|rid2": "accept"|"reject"} from the human review queue.
    """
    decisions = decisions or {}
    by_rid = {r.rid: r for r in records}
    uf = UnionFind(list(by_rid))
    merges, review = [], []
    for a, b in itertools.combinations(records, 2):
        if a.source == b.source == "subscriber":
            continue  # one SIM = one registry row; never merge registry rows with each other directly
        key = "|".join(sorted((a.rid, b.rid)))
        dec = decisions.get(key)
        if dec == "reject":
            continue
        s, why = score_pair(a, b)
        if s < REVIEW_T and dec != "accept":
            continue
        if s >= MERGE_T or dec == "accept":
            uf.union(a.rid, b.rid)
            merges.append({"a": a.rid, "b": b.rid, "score": s, "why": why,
                           "mode": "human-accepted" if dec == "accept" and s < MERGE_T else "auto"})
        else:
            review.append({"key": key, "a": a.rid, "b": b.rid, "score": s, "why": why,
                           "a_name": a.name, "b_name": b.name, "a_src": a.source, "b_src": b.source})
    groups: dict[str, list[PersonRecord]] = {}
    for r in records:
        groups.setdefault(uf.find(r.rid), []).append(r)
    # drop review items that ended up in the same entity anyway
    review = [x for x in review if uf.find(x["a"]) != uf.find(x["b"])]
    best: dict = {}
    for x in review:  # one queue item per pair of resulting entities (keep the strongest evidence)
        k = tuple(sorted((uf.find(x["a"]), uf.find(x["b"]))))
        if k not in best or x["score"] > best[k]["score"]:
            best[k] = x
    review = sorted(best.values(), key=lambda x: -x["score"])

    src_rank = {"kyc": 0, "subscriber": 1, "rto": 2, "fir": 3, "kycsig": 4, "social": 5}
    entities = []
    for recs in groups.values():
        recs.sort(key=lambda r: (src_rank.get(r.source, 9), r.rid))
        # display name: most complete full name (prefer KYC/registry, title case)
        votes: dict[str, float] = {}
        for r in recs:
            if len(canon(r.name)) >= 2 and "." not in r.name and r.source not in ("social", "kycsig"):
                w = {"subscriber": 1.2, "rto": 1.1, "kyc": 1.0, "fir": 0.8}.get(r.source, 0.5)
                votes[r.name.title()] = votes.get(r.name.title(), 0) + w
        name = max(votes, key=votes.get) if votes else recs[0].name.title()
        aliases = sorted({n for r in recs for n in _names(r) if n.lower() != name.lower()})
        phones = sorted({p for r in recs for p in r.phones})
        dob = next((r.dob for r in recs if r.dob), "")
        city = next((r.city for r in recs if r.city), "")
        ids = sorted({f"{r.id_type}:{r.id_number}" for r in recs if r.id_number})
        slug = re.sub(r"[^a-z]+", "-", name.lower()).strip("-")
        entities.append({"name": name, "slug": slug, "records": [r.rid for r in recs],
                         "sources": sorted({r.source for r in recs}), "aliases": aliases, "phones": phones,
                         "dob": dob, "city": city, "ids": ids})
    # stable, unique ids
    seen = {}
    for e in sorted(entities, key=lambda e: e["records"][0]):
        base = "P:" + e["slug"]
        n = seen.get(base, 0)
        seen[base] = n + 1
        e["id"] = base if n == 0 else f"{base}-{n + 1}"
    merged_ids = {m["a"]: uf.find(m["a"]) for m in merges}
    return entities, merges, review, {rid: uf.find(rid) for rid in by_rid}
