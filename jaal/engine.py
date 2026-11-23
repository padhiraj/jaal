"""JAAL analysis engine: knowledge graph construction + network analytics + explanations."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import algorithms as alg
from .ingest import Dataset, load, manifest, norm_phone
from .resolve import resolve

NOI_SIZE = 30
EV_CAP = 25  # evidence rows kept per edge (the total count is always kept)

TYPE_LABEL = {"P": "Person", "PH": "Phone", "AC": "Account", "ORG": "Organisation", "VH": "Vehicle", "CASE": "Case",
              "LOC": "Location", "SM": "Social handle", "IMEI": "Handset", "GRP": "Group", "U": "Unattributed phone"}

WEIGHTS = {"ppr": 0.25, "betweenness": 0.25, "eigenvector": 0.15, "disruption": 0.20, "flags": 0.15}


def ev(doc, line, text):
    return {"doc": doc, "loc": f"L{line}", "text": text[:220]}


class Engine:
    def __init__(self, data_dir: str | Path, state_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path = self.state_dir / "er_decisions.json"
        self.decisions = json.loads(self.decisions_path.read_text()) if self.decisions_path.exists() else {}
        self.build()

    # ================================================================== build
    def build(self):
        t0 = time.time()
        self.ds: Dataset = load(self.data_dir)
        self.entities, self.merges, self.review, root = resolve(self.ds.persons, self.decisions)
        self.rec = {r.rid: r for r in self.ds.persons}
        self.rid2pid = {}
        for e in self.entities:
            for rid in e["records"]:
                self.rid2pid[rid] = e["id"]
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self._build_kg()
        self._build_person_graph()
        self._analyse()
        self.build_ms = int((time.time() - t0) * 1000)
        self.built_at = dt.datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------------ KG primitives
    def node(self, nid, ntype, label, **attrs):
        n = self.nodes.get(nid)
        if not n:
            n = self.nodes[nid] = {"id": nid, "type": ntype, "label": label, "evidence": [], **attrs}
        else:
            n.update({k: v for k, v in attrs.items() if v not in (None, "", [])})
        return n

    def edge(self, u, v, etype, evidence=None, directed=False, weight=1.0, **attrs):
        key = (u, v, etype) if directed else (min(u, v), max(u, v), etype)
        e = self.edges.get(key)
        if not e:
            e = self.edges[key] = {"id": f"E{len(self.edges)}", "u": key[0], "v": key[1], "type": etype,
                                   "directed": directed, "count": 0, "weight": 0.0, "evidence": [], **attrs}
        e["count"] += 1
        e["weight"] += weight
        if evidence and len(e["evidence"]) < EV_CAP:
            e["evidence"].append(evidence)
        return e

    # ------------------------------------------------------------------ KG
    def _build_kg(self):
        ds = self.ds
        # persons
        for e in self.entities:
            self.node(e["id"], "P", e["name"], aliases=e["aliases"], phones=e["phones"], dob=e["dob"],
                      city=e["city"], records=e["records"], sources=e["sources"], ids=e["ids"])
            for rid in e["records"]:
                r = self.rec[rid]
                self.nodes[e["id"]]["evidence"].append(r.evidence.to_dict())
        self.phone_claims = defaultdict(list)  # phone -> [(pid, source, evidence)]
        for r in ds.persons:
            pid = self.rid2pid[r.rid]
            for ph in r.phones:
                self.phone_claims[ph].append((pid, r.source, r.evidence.to_dict()))
            if r.source == "kyc":
                acct = r.meta["account"]
                self.node(f"AC:{acct}", "AC", f"A/c …{acct[-4:]}", account=acct, bank=ds.accounts[acct]["bank"])
                self.edge(pid, f"AC:{acct}", "HOLDS", r.evidence.to_dict(), directed=True)
            elif r.source == "kycsig":
                acct, org = r.meta["account"], r.meta["org"]
                oid = "ORG:" + re.sub(r"[^a-z]+", "-", org.lower()).strip("-")
                self.node(oid, "ORG", org)
                self.node(f"AC:{acct}", "AC", f"A/c …{acct[-4:]}", account=acct, bank=ds.accounts[acct]["bank"])
                self.edge(oid, f"AC:{acct}", "HOLDS", r.evidence.to_dict(), directed=True)
                self.edge(pid, oid, "CONTROLS", r.evidence.to_dict(), directed=True)
            elif r.source == "rto":
                reg = r.meta["vehicle"]
                self.node(f"VH:{reg}", "VH", reg, model=ds.vehicles[reg].get("model"))
                self.edge(pid, f"VH:{reg}", "OWNS", r.evidence.to_dict(), directed=True)
            elif r.source == "social":
                h = r.meta["handle"]
                self.node(f"SM:{h}", "SM", h, display=r.name)
                self.edge(pid, f"SM:{h}", "OPERATES", r.evidence.to_dict(), directed=True)
            elif r.source == "fir":
                c = r.meta["case"]
                self.edge(pid, f"CASE:{c}", "NAMED_IN", r.evidence.to_dict(), directed=True,
                          role=r.meta["role"], status=r.meta["status"])
        # handsets: which SIMs used which IMEI
        imei_sims = defaultdict(lambda: defaultdict(list))
        for c in ds.calls:
            imei_sims[c["imei"]][c["a"]].append(c)
        self.shared_imeis = {i: s for i, s in imei_sims.items() if len(s) > 1}
        # phone ownership (+ ghost-identity inference)
        self.phone_owner, self.inferences = {}, []
        for ph, claims in self.phone_claims.items():
            sub = [c for c in claims if c[1] == "subscriber"]
            self.phone_owner[ph] = (sub or claims)[0][0]
        ent = {e["id"]: e for e in self.entities}
        self.ghosts = set()
        for imei, sims in self.shared_imeis.items():
            owners = {s: self.phone_owner.get(s) for s in sims}
            real = [o for o in owners.values() if o and ent[o]["sources"] != ["subscriber"]]
            if len(set(real)) != 1:
                continue
            real = real[0]
            for sim, o in owners.items():
                if o and o != real and ent[o]["sources"] == ["subscriber"]:
                    self.ghosts.add(o)
                    self.phone_owner[sim] = real
                    rows = sims[sim]
                    own_rows = [c for s, rs in sims.items() if owners[s] == real for c in rs]
                    self.inferences.append({
                        "phone": sim, "imei": imei, "registered_to": o, "attributed_to": real, "confidence": 0.85,
                        "attributed_label": ent[real]["name"], "registered_label": ent[o]["name"],
                        "reason": (f"SIM {sim} is registered to '{ent[o]['name']}', an identity that appears in no other "
                                   f"source. It ran in handset IMEI {imei} ({len(rows)} calls, {rows[0]['ts'][:10]} → "
                                   f"{rows[-1]['ts'][:10]}), the same handset in which {ent[real]['name']}'s own "
                                   f"registered SIM was used on {', '.join(sorted({c['ts'][:10] for c in own_rows}))}."),
                        "evidence": [ev("cdr.csv", c["line"], self._cdr_line(c["line"])) for c in own_rows[:4]] +
                                    [ev("cdr.csv", c["line"], self._cdr_line(c["line"])) for c in rows[:3]]})
        for g in self.ghosts:
            self.nodes[g]["ghost"] = True
        # phones + ownership edges
        for ph, claims in self.phone_claims.items():
            self.node(f"PH:{ph}", "PH", ph)
            for pid, src, e in claims:
                et = "REGISTERED_TO" if src == "subscriber" else "USES"
                self.edge(f"PH:{ph}", pid, et, e, directed=True)
        for inf in self.inferences:
            self.edge(f"PH:{inf['phone']}", inf["attributed_to"], "INFERRED_USER", inf["evidence"][0], directed=True,
                      confidence=inf["confidence"], reason=inf["reason"])
        for imei, sims in self.shared_imeis.items():
            self.node(f"IMEI:{imei}", "IMEI", f"IMEI …{imei[-5:]}", imei=imei)
            for sim, rows in sims.items():
                self.node(f"PH:{sim}", "PH", sim)
                for c in rows[:3]:
                    self.edge(f"PH:{sim}", f"IMEI:{imei}", "USED_IN", ev("cdr.csv", c["line"], self._cdr_line(c["line"])))
        # calls
        self.daily_calls = defaultdict(Counter)  # phone -> day -> n
        for c in ds.calls:
            for p in (c["a"], c["b"]):
                self.node(f"PH:{p}", "PH", p)
                self.daily_calls[p][c["ts"][:10]] += 1
            e = self.edge(f"PH:{c['a']}", f"PH:{c['b']}", "CALLED", ev("cdr.csv", c["line"], self._cdr_line(c["line"])),
                          first=c["ts"], duration=0)
            e["duration"] += c["dur"]
            e["last"] = c["ts"]
        # accounts + transfers
        for acct, row in ds.accounts.items():
            self.node(f"AC:{acct}", "AC", f"A/c …{acct[-4:]}", account=acct, bank=row["bank"])
        for t in ds.txns:
            for a in (t["src"], t["dst"]):
                self.node(f"AC:{a}", "AC", f"A/c …{a[-4:]}", account=a)
            e = self.edge(f"AC:{t['src']}", f"AC:{t['dst']}", "TRANSFERRED",
                          ev("transactions.csv", t["line"], self.ds.docs["transactions.csv"]["lines"][t["line"] - 1]),
                          directed=True, amount=0, first=t["ts"])
            e["amount"] += t["amount"]
            e["last"] = t["ts"]
        # vehicles, ANPR, co-location
        by_cam = defaultdict(list)
        for h in ds.anpr:
            self.node(f"VH:{h['reg']}", "VH", h["reg"])
            self.node(f"LOC:{h['camera']}", "LOC", h["camera"])
            self.edge(f"VH:{h['reg']}", f"LOC:{h['camera']}", "SEEN_AT",
                      ev("anpr.csv", h["line"], ds.docs["anpr.csv"]["lines"][h["line"] - 1]))
            by_cam[h["camera"]].append(h)
        self.colocations = defaultdict(list)
        for cam, hits in by_cam.items():
            hits.sort(key=lambda h: h["ts"])
            for i, h in enumerate(hits):
                t1 = dt.datetime.fromisoformat(h["ts"])
                for h2 in hits[i + 1:]:
                    t2 = dt.datetime.fromisoformat(h2["ts"])
                    if (t2 - t1).total_seconds() > 600:
                        break
                    if h2["reg"] != h["reg"]:
                        self.colocations[tuple(sorted((h["reg"], h2["reg"])))].append((cam, h, h2))
        for (a, b), hits in self.colocations.items():
            if len(hits) >= 2:
                for cam, h, h2 in hits:
                    self.edge(f"VH:{a}", f"VH:{b}", "CO_LOCATED",
                              ev("anpr.csv", h["line"], f"{h['reg']} @ {cam} {h['ts']}  ⇄  {h2['reg']} @ {h2['ts']}"))
        # social
        for m in ds.messages:
            self.node(f"SM:{m['sender']}", "SM", m["sender"])
            e_ = ev("social.json", m["line"], f"{m['ts']} {m['sender']} → {m['to']}: {m['text']}")
            if m["to"].startswith("#"):
                self.node(f"GRP:{m['to']}", "GRP", m["to"])
                self.edge(f"SM:{m['sender']}", f"GRP:{m['to']}", "POSTED_IN", e_)
            else:
                self.node(f"SM:{m['to']}", "SM", m["to"])
                self.edge(f"SM:{m['sender']}", f"SM:{m['to']}", "MESSAGED", e_)
        # cases
        for cno, c in ds.cases.items():
            self.node(f"CASE:{cno}", "CASE", cno, station=c["station"], state=c["state"], date=c["date"],
                      sections=c["sections"], mo=c["mo"], doc=c["doc"])
            for p in c["phones"]:
                self.node(f"PH:{p['phone']}", "PH", p["phone"])
                self.edge(f"CASE:{cno}", f"PH:{p['phone']}", "MENTIONS", ev(c["doc"], p["line"], p["text"]), directed=True)
            for a in c["accounts"]:
                self.node(f"AC:{a['account']}", "AC", f"A/c …{a['account'][-4:]}", account=a["account"])
                self.edge(f"CASE:{cno}", f"AC:{a['account']}", "MENTIONS", ev(c["doc"], a["line"], a["text"]), directed=True)
            for v in c["vehicles"]:
                self.node(f"VH:{v['reg']}", "VH", v["reg"])
                self.edge(f"CASE:{cno}", f"VH:{v['reg']}", "MENTIONS", ev(c["doc"], v["line"], v["text"]), directed=True)
        # unattributed phones get a placeholder actor so they still appear in the person network
        for nid, n in list(self.nodes.items()):
            if n["type"] == "PH" and n["label"] not in self.phone_owner:
                self.phone_owner[n["label"]] = f"U:{n['label']}"
                self.node(f"U:{n['label']}", "U", f"Unknown ☎ {n['label']}", phones=[n["label"]])

    def _cdr_line(self, ln):
        return self.ds.docs["cdr.csv"]["lines"][ln - 1]

    # ------------------------------------------------------------------ asset ownership helpers
    def assets_of(self, pid):
        out = {"phones": [], "accounts": [], "orgs": [], "vehicles": [], "handles": []}
        out["phones"] = sorted(p for p, o in self.phone_owner.items() if o == pid)
        for (u, v, t), e in self.edges.items():
            if u != pid:
                continue
            if t == "HOLDS":
                out["accounts"].append(v[3:])
            elif t == "CONTROLS":
                out["orgs"].append(v)
                out["accounts"] += [vv[3:] for (uu, vv, tt) in self.edges if uu == v and tt == "HOLDS"]
            elif t == "OWNS":
                out["vehicles"].append(v[3:])
            elif t == "OPERATES":
                out["handles"].append(v[3:])
        return out

    # ------------------------------------------------------------------ person projection
    def _build_person_graph(self):
        self.acct_owner, self.veh_owner, self.handle_owner = {}, {}, {}
        self.person_ids = [n for n, d in self.nodes.items() if d["type"] in ("P", "U") and not d.get("ghost")]
        self.pg_edges: dict[tuple, dict] = {}
        for pid in self.person_ids:
            a = self.assets_of(pid)
            for x in a["accounts"]:
                self.acct_owner[x] = pid
            for x in a["vehicles"]:
                self.veh_owner[x] = pid
            for x in a["handles"]:
                self.handle_owner[x] = pid
            self.nodes[pid]["assets"] = a
        for pid in self.person_ids:  # unattributed phone placeholders
            if pid.startswith("U:"):
                self.nodes[pid]["assets"] = {"phones": [pid[2:]], "accounts": [], "orgs": [], "vehicles": [], "handles": []}

        def link(p1, p2, channel, w, kg_edge, **extra):
            if not p1 or not p2 or p1 == p2 or p1 not in self.nodes or p2 not in self.nodes:
                return
            if self.nodes[p1].get("ghost") or self.nodes[p2].get("ghost"):
                return
            key = (min(p1, p2), max(p1, p2))
            e = self.pg_edges.setdefault(key, {"u": key[0], "v": key[1], "weight": 0.0, "channels": Counter(),
                                               "kg_edges": [], "amount": 0})
            e["weight"] += w
            e["channels"][channel] += extra.get("n", 1)
            e["amount"] += extra.get("amount", 0)
            if kg_edge and kg_edge["id"] not in e["kg_edges"]:
                e["kg_edges"].append(kg_edge["id"])

        for (u, v, t), e in self.edges.items():
            if t == "CALLED":
                link(self.phone_owner.get(u[3:]), self.phone_owner.get(v[3:]), "calls", e["count"], e, n=e["count"])
            elif t == "TRANSFERRED":
                link(self.acct_owner.get(u[3:]), self.acct_owner.get(v[3:]), "transfers", 3 * e["count"], e,
                     n=e["count"], amount=e["amount"])
            elif t == "CO_LOCATED":
                link(self.veh_owner.get(u[3:]), self.veh_owner.get(v[3:]), "co_location", 5 * e["count"], e, n=e["count"])
            elif t == "MESSAGED":
                link(self.handle_owner.get(u[3:]), self.handle_owner.get(v[3:]), "social", e["count"], e, n=e["count"])
        # group co-membership (posted in the same group)
        grp = defaultdict(dict)
        for (u, v, t), e in self.edges.items():
            if t == "POSTED_IN":
                grp[v][u] = e
        for g_, members in grp.items():
            ms = list(members)
            for i, a in enumerate(ms):
                for b in ms[i + 1:]:
                    link(self.handle_owner.get(a[3:]), self.handle_owner.get(b[3:]), "social",
                         min(members[a]["count"], members[b]["count"]), members[a],
                         n=min(members[a]["count"], members[b]["count"]))
        # co-accused / co-named in an FIR
        self.case_people = defaultdict(dict)  # case -> pid -> role
        for (u, v, t), e in self.edges.items():
            if t == "NAMED_IN":
                self.case_people[v][u] = e["role"]
        for c, ppl in self.case_people.items():
            acc = [p for p, r in ppl.items() if r == "accused"]
            for i, a in enumerate(acc):
                for b in acc[i + 1:]:
                    link(a, b, "co_accused", 4, self.edges.get((a, c, "NAMED_IN")))
        self.adj = {p: {} for p in self.person_ids}
        for (a, b), e in self.pg_edges.items():
            self.adj[a][b] = e["weight"]
            self.adj[b][a] = e["weight"]

    # ================================================================== analytics
    def _analyse(self):
        adj = self.adj
        ppl = self.person_ids
        self.accused = sorted({p for c in self.case_people.values() for p, r in c.items() if r == "accused"})
        self.complainants = sorted({p for c in self.case_people.values() for p, r in c.items() if r == "complainant"})
        # suspicion seeds = everyone named as accused in any FIR
        self.seeds = {p: 1.0 for p in self.accused}
        self.ppr = alg.pagerank(adj, self.seeds)
        self.deg = {p: len(adj[p]) for p in ppl}
        self.bc_global = alg.betweenness(adj)
        self.community = alg.louvain(adj)
        self.modularity = alg.modularity(adj, self.community)
        # Network of interest (NOI) = FIR-accused seeds + the people that suspicion diffuses to most
        # (personalised PageRank), excluding complainants. Structural metrics are computed on the NOI so
        # that busy-but-benign civilians (delivery agents, families) do not dominate the ranking.
        self._benign_hubs()
        self._probable_victims()
        skip = self.benign_hubs | self.victims | set(self.complainants)
        ranked_ppr = sorted((p for p in ppl if p not in skip), key=lambda p: -self.ppr[p])
        self.noi = set(self.accused) | set(ranked_ppr[:NOI_SIZE])
        self._money()
        self._structural()
        self._alerts()
        # alert-driven expansion: anyone with a high-severity financial/telecom alert joins the NOI
        flagged = {p for a in self.alerts if a["severity"] >= 3 for p in a["entities"]
                   if p in self.adj and p not in skip and not self.nodes[p].get("ghost")}
        self.noi_expanded = sorted(flagged - self.noi)
        if self.noi_expanded:
            self.noi |= flagged
            self._structural()
            self._alerts()
        self._score()
        self._roles()
        self._cross_case()
        self._predicted_links()
        self._key_player_set()

    def _structural(self):
        adj, ppl = self.adj, self.person_ids
        sub = {p: {q: w for q, w in adj[p].items() if q in self.noi} for p in self.noi}
        self.noi_adj = sub
        bc_noi, ev_noi = alg.betweenness(sub, weighted=True), alg.eigenvector(sub)
        self.bc = {p: bc_noi.get(p, 0.0) for p in ppl}
        self.ev = {p: ev_noi.get(p, 0.0) for p in ppl}
        base = alg.global_efficiency(sub)
        self.disruption = {p: 0.0 for p in ppl}
        for p in self.noi:
            after = alg.global_efficiency(sub, removed=p, denom_n=len(sub))
            self.disruption[p] = max(0.0, (base - after) / base) if base else 0
        self.noi_efficiency = base

    def _probable_victims(self):
        """People who paid >= Rs 50k into an FIR-accused person's account but have only a weak tie
        (<= 6 calls, no other channel) to them: likely unreported victims, not accomplices."""
        self.victims, self.victim_why = set(), {}
        acc = set(self.accused)
        payees, receives_from_acc = defaultdict(list), set()
        for t in self.ds.txns:
            src, dst = self.acct_owner.get(t["src"]), self.acct_owner.get(t["dst"])
            if dst in acc and src and t["amount"] >= 50000:
                payees[src].append(dst)
            if src in acc and dst:
                receives_from_acc.add((dst, src))
        for t in self.ds.txns:
            src, dst = self.acct_owner.get(t["src"]), self.acct_owner.get(t["dst"])
            if not src or src in acc or dst not in acc or t["amount"] < 50000:
                continue
            if (len(payees[src]) > 2 or len(set(payees[src])) > 1 or (src, dst) in receives_from_acc
                    or self.nodes[src].get("assets", {}).get("orgs")):
                continue  # repeated / multi-party payments or two-way money is not a victim pattern
            e = self.pg_edges.get((min(src, dst), max(src, dst)))
            if e and e["channels"].get("calls", 0) <= 6 and set(e["channels"]) <= {"calls", "transfers"}:
                self.victims.add(src)
                self.victim_why[src] = (f"Paid Rs {t['amount']:,} on {t['ts'][:10]} to an account of FIR-accused "
                                        f"{self.nodes[dst]['label']} ('{t['narration']}') after only "
                                        f"{e['channels'].get('calls', 0)} calls with them.")

    def _benign_hubs(self):
        """High-degree actors whose ties are many, weak and mutually unconnected (delivery agents,
        call centres, shopkeepers). Kept in the graph, but excluded from the network of interest."""
        adj = self.adj
        degs = sorted(self.deg.values())
        med_deg = degs[len(degs) // 2] or 1
        ties = sorted(w for p in adj for w in adj[p].values())
        med_tie = ties[len(ties) // 2] if ties else 1
        self.hub_stats, self.benign_hubs = {}, set()
        for p in self.person_ids:
            nb = list(adj[p])
            k = len(nb)
            if k < max(8, 3 * med_deg):
                continue
            links = sum(1 for i, a in enumerate(nb) for b in nb[i + 1:] if b in adj[a])
            clustering = 2 * links / (k * (k - 1))
            mean_tie = sum(adj[p].values()) / k
            self.hub_stats[p] = {"degree": k, "median_degree": med_deg, "mean_tie": round(mean_tie, 1),
                                 "median_tie": med_tie, "clustering": round(clustering, 3)}
            if mean_tie < med_tie and clustering < 0.15 and p not in self.accused:
                self.benign_hubs.add(p)

    # ------------------------------------------------------------------ money
    def _money(self):
        self.money = defaultdict(lambda: {"in": 0, "out": 0, "in_src": set(), "out_dst": set()})
        for (u, v, t), e in self.edges.items():
            if t != "TRANSFERRED":
                continue
            a, b = self.acct_owner.get(u[3:]), self.acct_owner.get(v[3:])
            if a == b:
                continue
            if a:
                self.money[a]["out"] += e["amount"]
                self.money[a]["out_dst"].add(b)
            if b:
                self.money[b]["in"] += e["amount"]
                self.money[b]["in_src"].add(a)

    # ------------------------------------------------------------------ alerts
    def _alerts(self):
        A = []
        self.flags = defaultdict(float)
        name = lambda p: self.nodes[p]["label"] if p in self.nodes else p
        days = sorted({c["ts"][:10] for c in self.ds.calls})
        last_day = days[-1] if days else ""
        case_dates = {c: self.ds.cases[c[5:]]["date"] for c in self.case_people}

        def add(kind, sev, title, detail, ents, evidence):
            A.append({"id": f"AL{len(A) + 1:03d}", "type": kind, "severity": sev, "title": title, "detail": detail,
                      "entities": ents, "entity_labels": [name(p) for p in ents], "evidence": evidence[:8]})
            for p in ents:
                if p in self.nodes and self.nodes[p]["type"] in ("P", "U"):
                    self.flags[p] += sev

        # 1. handset sharing / burner rotation / ghost identities
        for inf in self.inferences:
            add("burner", 3, f"Burner SIM {inf['phone']} attributed to {name(inf['attributed_to'])}",
                inf["reason"] + " Registered subscriber flagged as a likely fake (ghost) identity.",
                [inf["attributed_to"], inf["registered_to"]], inf["evidence"])
        for imei, sims in self.shared_imeis.items():
            spans = sorted(((rows[0]["ts"], rows[-1]["ts"], s, len(rows)) for s, rows in sims.items()))
            heavy = [x for x in spans if x[3] >= 10]
            for a, b in zip(heavy, heavy[1:]):
                gap = (dt.datetime.fromisoformat(b[0]) - dt.datetime.fromisoformat(a[1])).days
                if 0 <= gap <= 3:
                    owner = self.phone_owner.get(a[2])
                    add("rotation", 3, f"SIM rotation in handset IMEI …{imei[-5:]}",
                        f"SIM {a[2]} went silent on {a[1][:10]} and SIM {b[2]} began in the same handset on "
                        f"{b[0][:10]} ({gap} day gap) — classic burner rotation.", [owner] if owner else [],
                        [ev("cdr.csv", sims[a[2]][-1]["line"], self._cdr_line(sims[a[2]][-1]["line"])),
                         ev("cdr.csv", sims[b[2]][0]["line"], self._cdr_line(sims[b[2]][0]["line"]))])
        # 2. temporal spikes (robust z-score on daily call volume per person)
        self.person_daily = {}
        for p in self.person_ids:
            cnt = Counter()
            for ph in self.nodes[p].get("assets", {}).get("phones", []):
                cnt.update(self.daily_calls.get(ph, {}))
            self.person_daily[p] = cnt
            series = [cnt.get(d, 0) for d in days]
            if sum(series) < 30:
                continue
            med = statistics.median(series)
            mad = statistics.median([abs(x - med) for x in series]) or 1.0
            hot = [(d, x, (x - med) / (1.4826 * mad)) for d, x in zip(days, series)
                   if (x - med) / (1.4826 * mad) >= 4 and x >= max(8, 3 * med)]
            if hot:
                near = []
                circle = {p} | set(self.adj[p])
                for d, *_ in hot:
                    for cid, cd in case_dates.items():
                        if cd and 0 <= (dt.date.fromisoformat(cd) - dt.date.fromisoformat(d)).days <= 6 \
                                and circle & set(self.case_people.get(cid, {})):
                            near.append(cid[5:])
                hot_s = ", ".join(f"{d[5:]} ({x} calls, z={z:.1f})" for d, x, z in hot[:4])
                add("spike", 2 if near else 1, f"Communication spike: {name(p)}",
                    f"Daily call volume far above this person's baseline (median {med:.0f}/day): {hot_s}."
                    + (f" Spike precedes reporting of case(s) {', '.join(sorted(set(near)))}." if near else ""),
                    [p], [self._sample_call_ev(p, hot[0][0])])
        # 3. going dark
        for ph, cnt in self.daily_calls.items():
            if len(cnt) < 15:
                continue
            last = max(cnt)
            gap = (dt.date.fromisoformat(last_day) - dt.date.fromisoformat(last)).days
            rate = sum(cnt.values()) / len(cnt)
            if gap >= 5 and rate >= 2:
                owner = self.phone_owner.get(ph)
                near = [cid[5:] for cid, cd in case_dates.items()
                        if cd and abs((dt.date.fromisoformat(cd) - dt.date.fromisoformat(last)).days) <= 2]
                add("dark", 2 if near else 1, f"Phone went dark: {ph} ({name(owner)})",
                    f"Averaged {rate:.1f} calls/day for {len(cnt)} active days, then silent from {last} "
                    f"({gap} days before data end)." + (f" Coincides with case {', '.join(near)}." if near else ""),
                    [owner] if owner else [], [])
        # 4. structuring
        outs = defaultdict(list)
        for t in self.ds.txns:
            if 45000 <= t["amount"] < 50000:
                outs[t["src"]].append(t)
        for acct, ts in outs.items():
            if len(ts) >= 3:
                owner = self.acct_owner.get(acct)
                tot = sum(t["amount"] for t in ts)
                dsts = Counter(self._acct_label(t["dst"]) for t in ts)
                add("structuring", 3, f"Structuring from {self._acct_label(acct)}",
                    f"{len(ts)} transfers of Rs 45,000–49,999 (just under the Rs 50,000 reporting threshold), "
                    f"total Rs {tot:,}, to {', '.join(f'{k} ×{v}' for k, v in dsts.items())}.",
                    [owner] if owner else [], [self._tx_ev(t) for t in ts])
        # 5. pass-through (mule) accounts
        inflow, outflow = defaultdict(list), defaultdict(list)
        for t in self.ds.txns:
            inflow[t["dst"]].append(t)
            outflow[t["src"]].append(t)
        for acct, ins in inflow.items():
            srcs = {t["src"] for t in ins}
            tin = sum(t["amount"] for t in ins)
            if len(srcs) < 2 or tin < 200000:
                continue
            fast = 0
            for t in outflow.get(acct, []):
                tt = dt.datetime.fromisoformat(t["ts"])
                if any(0 <= (tt - dt.datetime.fromisoformat(i["ts"])).total_seconds() <= 172800 for i in ins):
                    fast += t["amount"]
            if fast / tin >= 0.7:
                owner = self.acct_owner.get(acct)
                add("mule", 3, f"Pass-through account: {self._acct_label(acct)}",
                    f"Received Rs {tin:,} from {len(srcs)} sources; {fast / tin:.0%} left again within 48 hours.",
                    [owner] if owner else [], [self._tx_ev(t) for t in ins[:3] + outflow[acct][:3]])
        # 6. round-tripping
        dadj = defaultdict(set)
        for (u, v, t), e in self.edges.items():
            if t == "TRANSFERRED" and e["amount"] >= 40000:
                dadj[u].add(v)
        for cyc in alg.simple_cycles(dadj, max_len=5):
            legs = [self.edges[(cyc[i], cyc[(i + 1) % len(cyc)], "TRANSFERRED")] for i in range(len(cyc))]
            owners = [self.acct_owner.get(c[3:]) for c in cyc]
            add("cycle", 3, "Round-tripping: " + " → ".join(self._acct_label(c[3:]) for c in cyc) + " → ↺",
                "Funds left and returned to the starting account through " + str(len(cyc)) + " hops: " +
                "; ".join(f"{self._acct_label(l['u'][3:])}→{self._acct_label(l['v'][3:])} Rs {l['amount']:,}" for l in legs),
                sorted({o for o in owners if o}), [l["evidence"][0] for l in legs])
        # 7. co-location
        for (a, b), hits in self.colocations.items():
            if len(hits) >= 2:
                pa, pb = self.veh_owner.get(a), self.veh_owner.get(b)
                add("colocation", 2, f"Repeated co-location: {a} & {b}",
                    f"Seen together at the same camera within 10 minutes {len(hits)} times "
                    f"({', '.join(sorted({h[0] for h in hits}))}), mostly between midnight and 4 a.m."
                    f" Owners: {name(pa)}, {name(pb)}.", [x for x in (pa, pb) if x],
                    [ev("anpr.csv", h["line"], f"{h['reg']} @ {c} {h['ts']} ⇄ {h2['reg']} @ {h2['ts']}") for c, h, h2 in hits])
        self.alerts = sorted(A, key=lambda a: (-a["severity"], a["id"]))

    def _acct_label(self, acct):
        o = self.acct_owner.get(acct)
        row = self.ds.accounts.get(acct)
        if row and row["holder_type"] == "COMPANY":
            return row["holder_name"]
        return (self.nodes[o]["label"] if o else "unknown") + f" …{acct[-4:]}"

    def _tx_ev(self, t):
        return ev("transactions.csv", t["line"], self.ds.docs["transactions.csv"]["lines"][t["line"] - 1])

    def _sample_call_ev(self, p, day):
        phones = set(self.nodes[p]["assets"]["phones"])
        for c in self.ds.calls:
            if c["ts"].startswith(day) and (c["a"] in phones or c["b"] in phones):
                return ev("cdr.csv", c["line"], self._cdr_line(c["line"]))
        return {}

    # ------------------------------------------------------------------ scoring
    def _score(self):
        ppl = self.person_ids
        n = {"ppr": alg.normalise(self.ppr), "betweenness": alg.normalise(self.bc),
             "eigenvector": alg.normalise(self.ev), "disruption": alg.normalise(self.disruption),
             "flags": {p: min(1.0, self.flags.get(p, 0) / 6) for p in ppl}}
        rank = {k: {p: i + 1 for i, p in enumerate(sorted(ppl, key=lambda p: -v[p]))} for k, v in n.items()}
        self.scores = {}
        N = len(ppl)
        for p in ppl:
            parts = {k: round(WEIGHTS[k] * n[k][p], 4) for k in WEIGHTS}
            total = round(sum(parts.values()), 4)
            fl = [a for a in self.alerts if p in a["entities"]]
            expl = {
                "ppr": (f"Suspicion diffusion from {len(self.seeds)} FIR-accused seeds (personalised PageRank): "
                        f"rank #{rank['ppr'][p]} of {N}." + (" Directly seeded." if p in self.seeds else "")),
                "betweenness": f"Brokers {self.bc[p]:.1%} of strongest-tie paths inside the network of interest (rank #{rank['betweenness'][p]}).",
                "eigenvector": f"Tied to other influential suspects: eigenvector {self.ev[p]:.2f} (rank #{rank['eigenvector'][p]}).",
                "disruption": (f"Removing this person cuts the network-of-interest's communication efficiency by "
                               f"{self.disruption[p]:.1%} (rank #{rank['disruption'][p]})." if p in self.noi
                               else "Outside the network of interest."),
                "flags": f"{len(fl)} behavioural alert(s): " + ", ".join(sorted({a['type'] for a in fl})) if fl else "No behavioural alerts.",
            }
            self.scores[p] = {"total": total, "parts": parts, "explain": expl,
                              "raw": {"ppr": self.ppr[p], "betweenness": self.bc[p], "eigenvector": self.ev[p],
                                      "disruption": self.disruption[p], "flags": self.flags.get(p, 0), "degree": self.deg[p]}}
        self.ranking = sorted(ppl, key=lambda p: -self.scores[p]["total"])
        for i, p in enumerate(self.ranking):
            self.scores[p]["rank"] = i + 1

    def _roles(self):
        self.roles = {}
        named = defaultdict(list)
        for c, ppl in self.case_people.items():
            for p, r in ppl.items():
                named[p].append((c[5:], r))
        bc_rank = {p: i + 1 for i, p in enumerate(sorted(self.person_ids, key=lambda p: -self.bc[p]))}
        deg_rank = {p: i + 1 for i, p in enumerate(sorted(self.person_ids, key=lambda p: -self.deg[p]))}
        acc = set(self.accused)
        for p in self.person_ids:
            sc = self.scores[p]
            roles_named = {r for _, r in named[p]}
            two_hop = set()
            for nb in self.adj[p]:
                two_hop |= {nb} | set(self.adj[nb])
            two_hop.discard(p)
            acc_near = len(two_hop & acc)
            m = self.money.get(p, {"in": 0, "out": 0, "in_src": set(), "out_dst": set()})
            comms = {self.community[nb] for nb in self.adj[p]}
            orgs = self.nodes[p].get("assets", {}).get("orgs", [])
            if roles_named == {"complainant"}:
                role, why = "Victim / complainant", f"Named only as complainant in {', '.join(c for c, _ in named[p])}."
            elif p in self.victims:
                role, why = "Probable victim (unreported)", self.victim_why[p]
            elif orgs and (m["in"] + m["out"]) >= 500000:
                role, why = "Financial controller", (f"Controls {', '.join(self.nodes[o]['label'] for o in orgs)}; "
                                                     f"Rs {m['in']:,} in / Rs {m['out']:,} out across people.")
            elif sc["rank"] <= 5 and "accused" not in roles_named and acc_near >= 3:
                role, why = "Suspected orchestrator", (f"Priority rank #{sc['rank']}, yet never named in any FIR — "
                                                       f"insulated behind {acc_near} FIR-accused people within two hops.")
            elif m["in"] >= 200000 and len(m["in_src"]) >= 2 and m["out"] >= 0.6 * m["in"]:
                role, why = "Money mule", (f"Rs {m['in']:,} in from {len(m['in_src'])} sources, "
                                           f"{m['out'] / max(1, m['in']):.0%} forwarded.")
            elif bc_rank[p] <= 8 and len(comms) >= 2 and p in self.noi:
                role, why = "Broker / intermediary", (f"Betweenness rank #{bc_rank[p]}; links {len(comms)} "
                                                      f"different communities.")
            elif "accused" in roles_named:
                role, why = "Field operative", "Named as accused in " + ", ".join(c for c, r in named[p] if r == "accused") + "."
            elif p in self.benign_hubs:
                h = self.hub_stats[p]
                role, why = "Benign hub (likely)", (
                    f"{h['degree']} contacts (median person: {h['median_degree']}) but ties are weak (avg weight "
                    f"{h['mean_tie']} vs median {h['median_tie']}) and contacts don't know each other (clustering "
                    f"{h['clustering']}) — a service/delivery pattern. Excluded from the network of interest.")
            elif p in self.noi:
                role, why = "Associate", "Within the network of interest around case seeds."
            else:
                role, why = "Peripheral", "Not close to any case seed."
            self.roles[p] = {"role": role, "why": why, "named_in": named[p], "accused_within_2": acc_near}

    # ------------------------------------------------------------------ cross-case
    def _case_sets(self):
        sets = {}
        for cno, c in self.ds.cases.items():
            cid = f"CASE:{cno}"
            s = {p: f"named as {r}" for p, r in self.case_people.get(cid, {}).items() if r == "accused"}
            for ph in c["phones"]:
                o = self.phone_owner.get(ph["phone"])
                if ph.get("context_role") == "complainant" or o in self.complainants:
                    continue
                if o and o not in s:
                    s[o] = f"owns phone {ph['phone']} mentioned in FIR"
            for a in c["accounts"]:
                o = self.acct_owner.get(a["account"])
                if o and o not in s:
                    s[o] = f"controls a/c …{a['account'][-4:]} mentioned in FIR"
            for v in c["vehicles"]:
                o = self.veh_owner.get(v["reg"])
                if o and o not in s:
                    s[o] = f"owns vehicle {v['reg']} mentioned in FIR"
            sets[cno] = s
        return sets

    def _tfidf(self):
        docs = {c: re.findall(r"[a-z]{3,}", self.ds.cases[c]["text"].split("Brief facts")[-1].lower()) for c in self.ds.cases}
        df = Counter(w for ws in docs.values() for w in set(ws))
        N = len(docs)
        vec = {}
        for c, ws in docs.items():
            tf = Counter(ws)
            v = {w: (n / len(ws)) * math.log((1 + N) / (1 + df[w])) for w, n in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1
            vec[c] = {w: x / norm for w, x in v.items()}
        return vec

    def _cross_case(self):
        sets = self._case_sets()
        self.case_sets = sets
        vec = self._tfidf()
        cases = sorted(sets)
        self.case_links = []
        for i, a in enumerate(cases):
            for b in cases[i + 1:]:
                shared = set(sets[a]) & set(sets[b])
                best = None
                if shared:
                    p = sorted(shared)[0]
                    best = {"hops": 0, "path": [p]}
                else:
                    outside = {p for p in self.adj if p not in self.noi} | self.victims
                    for s in sets[a]:
                        for t in sets[b]:
                            if s not in self.adj or t not in self.adj:
                                continue
                            for banned in (self.benign_hubs | outside, self.benign_hubs | self.victims):
                                path = self._bfs_path(s, t, banned=banned)
                                if path:
                                    sc = self._path_strength(path)
                                    if best is None or sc > best["structural"]:
                                        best = {"hops": len(path) - 1, "path": path, "structural": sc}
                structural = 0 if not best else best.get("structural", 1.0)
                mo_a, mo_b = set(self.ds.cases[a]["mo"]), set(self.ds.cases[b]["mo"])
                mo_j = len(mo_a & mo_b) / len(mo_a | mo_b) if mo_a | mo_b else 0
                cos = sum(x * vec[b].get(w, 0) for w, x in vec[a].items())
                score = round(0.75 * structural + 0.15 * mo_j + 0.10 * cos, 3)
                strength = "strong" if score >= 0.45 else "moderate" if score >= 0.25 else "weak"
                hops_txt = []
                if best:
                    for x, y in zip(best["path"], best["path"][1:]):
                        hops_txt.append(self._hop_explain(x, y))
                self.case_links.append({
                    "a": a, "b": b, "score": score, "strength": strength, "structural": round(structural, 3),
                    "mo_overlap": sorted(mo_a & mo_b), "text_similarity": round(cos, 3),
                    "hops": best["hops"] if best else None, "path": best["path"] if best else [],
                    "path_labels": [self.nodes[x]["label"] for x in (best["path"] if best else [])],
                    "explain": ((f"{self.nodes[best['path'][0]]['label']} appears in both cases "
                                 f"({sets[a][best['path'][0]]} / {sets[b][best['path'][0]]})." if best and best["hops"] == 0
                                 else " → ".join(hops_txt)) if best else "No connecting path."),
                    "excluded": [self.nodes[h]["label"] for h in sorted(self.benign_hubs | self.victims)]})
        self.case_links.sort(key=lambda x: -x["score"])

    def _path_strength(self, path):
        """1/(1+hops); halved for every intermediary outside the network of interest; each hop is
        discounted by tie strength (a single message is weak evidence, 10+ interactions is full)."""
        sc = 1 / (len(path))
        for x in path[1:-1]:
            if x not in self.noi:
                sc *= 0.5
        for x, y in zip(path, path[1:]):
            w = self.adj[x][y]
            sc *= min(1.0, math.log1p(w) / math.log1p(10))
        return sc

    def _bfs_path(self, s, t, banned=frozenset()):
        from collections import deque
        prev, q = {s: None}, deque([s])
        while q:
            v = q.popleft()
            if v == t:
                break
            for w in sorted(self.adj[v]):
                if w not in prev and (w not in banned or w == t):
                    prev[w] = v
                    q.append(w)
        if t not in prev:
            return None
        path = [t]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        return path[::-1]

    def _hop_explain(self, x, y):
        e = self.pg_edges.get((min(x, y), max(x, y)))
        if not e:
            return f"{self.nodes[x]['label']} – {self.nodes[y]['label']}"
        ch = ", ".join(f"{k.replace('_', '-')} ×{v}" for k, v in e["channels"].items())
        return f"{self.nodes[x]['label']} –[{ch}]→ {self.nodes[y]['label']}"

    # ------------------------------------------------------------------ link prediction / key players
    def _predicted_links(self):
        cand = sorted(self.noi)
        self.predicted = []
        for a, b, s, common in alg.adamic_adar(self.noi_adj, cand)[:12]:
            self.predicted.append({"a": a, "b": b, "score": round(s, 3), "common": common,
                                   "a_label": self.nodes[a]["label"], "b_label": self.nodes[b]["label"],
                                   "explain": f"No direct record links them, but they share {len(common)} contacts: "
                                              + ", ".join(self.nodes[c]["label"] for c in common[:6]) + "."})

    def _key_player_set(self, k: int = 3):
        """Greedy selection of k people whose joint removal most fragments the network of interest."""
        chosen, base = [], alg.global_efficiency(self.adj, self.noi)
        cur = set(self.noi)
        steps = []
        for _ in range(k):
            best, best_eff = None, None
            for p in sorted(cur):
                eff = alg.global_efficiency(self.adj, cur - {p})
                if best_eff is None or eff < best_eff:
                    best, best_eff = p, eff
            chosen.append(best)
            cur.discard(best)
            steps.append({"pid": best, "label": self.nodes[best]["label"],
                          "efficiency_left": round(best_eff / base, 3) if base else 0})
        comps = alg.components(self.adj, cur)
        self.key_players = {"set": steps, "fragments_after": len(comps),
                            "largest_fragment": max(len(c) for c in comps) if comps else 0, "noi_size": len(self.noi)}

    # ================================================================== query API
    def summary(self):
        types = Counter(n["type"] for n in self.nodes.values())
        etypes = Counter(e["type"] for e in self.edges.values())
        return {
            "built_at": self.built_at, "build_ms": self.build_ms,
            "records": len(self.ds.persons), "entities": len([n for n in self.nodes.values() if n["type"] == "P"]),
            "auto_merges": len(self.merges), "review_queue": len(self.review),
            "node_types": {TYPE_LABEL.get(k, k): v for k, v in types.items()},
            "edge_types": dict(etypes), "kg_nodes": len(self.nodes), "kg_edges": len(self.edges),
            "person_nodes": len(self.person_ids), "person_edges": len(self.pg_edges),
            "communities": len(set(self.community.values())), "modularity": round(self.modularity, 3),
            "noi_size": len(self.noi), "alerts": len(self.alerts), "cases": len(self.ds.cases),
            "calls": len(self.ds.calls), "transactions": len(self.ds.txns), "anpr_hits": len(self.ds.anpr),
            "messages": len(self.ds.messages), "manifest": manifest(self.ds), "key_players": self.key_players,
            "weights": WEIGHTS,
        }

    def person_graph(self):
        nodes = []
        for p in self.person_ids:
            n = self.nodes[p]
            nodes.append({"id": p, "label": n["label"], "type": n["type"], "role": self.roles[p]["role"],
                          "community": self.community[p], "score": self.scores[p]["total"],
                          "rank": self.scores[p]["rank"], "noi": p in self.noi, "city": n.get("city", ""),
                          "accused": p in self.accused})
        edges = [{"u": e["u"], "v": e["v"], "weight": round(e["weight"], 2), "channels": dict(e["channels"]),
                  "amount": e["amount"]} for e in self.pg_edges.values()]
        return {"nodes": nodes, "edges": edges}

    def kg(self, focus=None, depth=2):
        keep = None
        if focus:
            nb = defaultdict(set)
            for (u, v, t) in self.edges:
                nb[u].add(v)
                nb[v].add(u)
            keep, frontier = {focus}, {focus}
            for _ in range(depth):
                frontier = {w for f in frontier for w in nb[f]} - keep
                keep |= frontier
        nodes = [{"id": n["id"], "label": n["label"], "type": n["type"], "ghost": n.get("ghost", False),
                  "score": self.scores.get(n["id"], {}).get("total", 0), "role": self.roles.get(n["id"], {}).get("role")}
                 for n in self.nodes.values() if keep is None or n["id"] in keep]
        ids = {n["id"] for n in nodes}
        edges = [{"id": e["id"], "u": e["u"], "v": e["v"], "type": e["type"], "count": e["count"],
                  "amount": e.get("amount")} for e in self.edges.values() if e["u"] in ids and e["v"] in ids]
        return {"nodes": nodes, "edges": edges}

    def dossier(self, pid):
        n = self.nodes.get(pid)
        if not n:
            return None
        out = {"id": pid, "label": n["label"], "type": TYPE_LABEL.get(n["type"], n["type"])}
        if n["type"] not in ("P", "U"):
            out["attrs"] = {k: v for k, v in n.items() if k not in ("evidence",)}
            out["edges"] = [self._edge_view(e) for e in self.edges.values() if pid in (e["u"], e["v"])][:60]
            return out
        recs = [self.rec[r] for r in n.get("records", [])]
        out.update({
            "aliases": n.get("aliases", []), "dob": n.get("dob"), "city": n.get("city"), "ids": n.get("ids", []),
            "ghost": n.get("ghost", False), "assets": n.get("assets", {}),
            "records": [{"rid": r.rid, "source": r.source, "name": r.name, "evidence": r.evidence.to_dict()} for r in recs],
            "merges": [m for m in self.merges if m["a"] in n.get("records", []) or m["b"] in n.get("records", [])][:20],
            "inferences": [i for i in self.inferences if i["attributed_to"] == pid or i["registered_to"] == pid],
            "score": self.scores.get(pid), "role": self.roles.get(pid), "community": self.community.get(pid),
            "noi": pid in self.noi,
            "money": {k: (sorted(self.nodes[x]["label"] for x in v if x) if isinstance(v, set) else v)
                      for k, v in self.money.get(pid, {"in": 0, "out": 0, "in_src": set(), "out_dst": set()}).items()},
            "alerts": [a for a in self.alerts if pid in a["entities"]],
        })
        conns = []
        for nb, w in sorted(self.adj.get(pid, {}).items(), key=lambda x: -x[1])[:15]:
            e = self.pg_edges[(min(pid, nb), max(pid, nb))]
            conns.append({"id": nb, "label": self.nodes[nb]["label"], "weight": round(w, 1),
                          "channels": dict(e["channels"]), "amount": e["amount"], "role": self.roles[nb]["role"],
                          "evidence": [ev_ for eid in e["kg_edges"][:3] for ev_ in self._edge_by_id(eid)["evidence"][:2]]})
        out["connections"] = conns
        return out

    def _edge_by_id(self, eid):
        if not hasattr(self, "_eid"):
            self._eid = {e["id"]: e for e in self.edges.values()}
        return self._eid[eid]

    def _edge_view(self, e):
        return {"id": e["id"], "u": e["u"], "v": e["v"], "type": e["type"], "count": e["count"],
                "u_label": self.nodes[e["u"]]["label"], "v_label": self.nodes[e["v"]]["label"],
                "amount": e.get("amount"), "evidence": e["evidence"][:5]}

    def timeline(self, pid):
        days = sorted({c["ts"][:10] for c in self.ds.calls})
        calls = self.person_daily.get(pid, {})
        accts = set(self.nodes.get(pid, {}).get("assets", {}).get("accounts", []))
        tin, tout = Counter(), Counter()
        for t in self.ds.txns:
            if t["dst"] in accts and t["src"] not in accts:
                tin[t["ts"][:10]] += t["amount"]
            if t["src"] in accts and t["dst"] not in accts:
                tout[t["ts"][:10]] += t["amount"]
        events = [{"day": self.ds.cases[c]["date"], "label": c} for c in self.ds.cases]
        return {"days": days, "calls": [calls.get(d, 0) for d in days], "money_in": [tin.get(d, 0) for d in days],
                "money_out": [tout.get(d, 0) for d in days], "events": events}

    def path(self, a, b):
        if a not in self.adj or b not in self.adj:
            return None
        p = alg.shortest_path(self.adj, a, b)
        if not p:
            return {"path": [], "hops": []}
        hops = []
        for x, y in zip(p, p[1:]):
            e = self.pg_edges[(min(x, y), max(x, y))]
            hops.append({"from": x, "to": y, "from_label": self.nodes[x]["label"], "to_label": self.nodes[y]["label"],
                         "channels": dict(e["channels"]), "amount": e["amount"],
                         "evidence": [ev_ for eid in e["kg_edges"][:4] for ev_ in self._edge_by_id(eid)["evidence"][:2]]})
        return {"path": p, "labels": [self.nodes[x]["label"] for x in p], "hops": hops}

    def targets(self, n=25):
        return [{"id": p, "label": self.nodes[p]["label"], "rank": self.scores[p]["rank"],
                 "score": self.scores[p]["total"], "parts": self.scores[p]["parts"], "role": self.roles[p]["role"],
                 "why": self.roles[p]["why"], "city": self.nodes[p].get("city", ""), "accused": p in self.accused}
                for p in self.ranking[:n]]

    def document(self, doc_id):
        d = self.ds.docs.get(doc_id)
        if not d:
            return None
        return {k: d[k] for k in ("id", "title", "kind", "sha256", "bytes")} | {"lines": d["lines"]}

    def search(self, q):
        q = q.lower().strip()
        qp = norm_phone(q)
        out = []
        for n in self.nodes.values():
            hay = " ".join([n["label"]] + n.get("aliases", []) + n.get("phones", [])).lower()
            if q in hay or (qp and qp in hay):
                out.append({"id": n["id"], "label": n["label"], "type": TYPE_LABEL.get(n["type"], n["type"])})
        return out[:20]

    # ------------------------------------------------------------------ mutations
    def ingest_fir(self, text: str) -> dict:
        m = re.search(r"FIR No\s*:\s*([A-Z]{3}/\d{4}/\d+)", text)
        if not m:
            raise ValueError("Could not find 'FIR No: XXX/YYYY/NNNN' in the text")
        fname = "FIR_" + m.group(1).replace("/", "_") + ".txt"
        (self.data_dir / "firs" / fname).write_text(text)
        before = {c["a"] + "|" + c["b"]: c["strength"] for c in self.case_links}
        self.build()
        new_links = [c for c in self.case_links if m.group(1) in (c["a"], c["b"])]
        return {"case": m.group(1), "file": fname, "links": new_links,
                "changed": [c for c in self.case_links if before.get(c["a"] + "|" + c["b"]) not in (None, c["strength"])]}

    def decide(self, key: str, decision: str):
        if decision not in ("accept", "reject"):
            raise ValueError("decision must be accept or reject")
        self.decisions[key] = decision
        self.decisions_path.write_text(json.dumps(self.decisions, indent=1))
        self.build()

    # ------------------------------------------------------------------ evaluation vs ground truth
    def evaluate(self):
        gt_path = self.data_dir / "ground_truth.json"
        if not gt_path.exists():
            return None
        gt = json.loads(gt_path.read_text())
        truth = gt["records"]
        rids = [r.rid for r in self.ds.persons if r.rid in truth]
        tp = fp = fn = 0
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                same = truth[a] == truth[b]
                pred = self.rid2pid[a] == self.rid2pid[b]
                tp += same and pred
                fp += (not same) and pred
                fn += same and not pred
        prec = tp / (tp + fp) if tp + fp else 1
        rec = tp / (tp + fn) if tp + fn else 1
        # map entity -> true key by majority of its records
        ent_truth, best_n = {}, {}
        for e in sorted(self.entities, key=lambda e: -len(e["records"])):
            keys = Counter(truth[r] for r in e["records"] if r in truth)
            if keys:
                k_ = keys.most_common(1)[0][0]
                if k_ not in best_n:
                    best_n[k_] = e["id"]
                    ent_truth[e["id"]] = k_
        synd = set(gt["syndicate"])
        def rank_of(order, key):
            for i, p in enumerate(order):
                if ent_truth.get(p) == key:
                    return i + 1
        by_deg = sorted(self.person_ids, key=lambda p: -self.deg[p])
        by_bc = sorted(self.person_ids, key=lambda p: -self.bc[p])
        k = len(synd)
        top = self.ranking[:k]
        hits = sum(1 for p in top if ent_truth.get(p) in synd)
        deg_hits = sum(1 for p in by_deg[:k] if ent_truth.get(p) in synd)
        role_ok = {}
        for p in self.person_ids:
            t = ent_truth.get(p)
            if t in synd:
                role_ok[gt["people"][t]["name"]] = {"true": gt["people"][t]["role"], "predicted": self.roles[p]["role"]}
        related = {"MUM/2026/0457", "HYD/2026/1182", "KOL/2026/0733"}
        cc_ok = sum(1 for c in self.case_links if ((c["a"] in related and c["b"] in related) == (c["strength"] != "weak")))
        return {"er": {"pairs_precision": round(prec, 4), "pairs_recall": round(rec, 4),
                       "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0,
                       "records": len(rids), "true_people": len(set(truth[r] for r in rids)), "entities": len(self.entities)},
                "kingpin_rank": {"jaal_priority": rank_of(self.ranking, gt["kingpin"]),
                                 "degree_only": rank_of(by_deg, gt["kingpin"]), "betweenness_only": rank_of(by_bc, gt["kingpin"])},
                "decoy_rank": {"jaal_priority": rank_of(self.ranking, gt["decoy"]), "degree_only": rank_of(by_deg, gt["decoy"])},
                "precision_at_k": {"k": k, "jaal": round(hits / k, 3), "degree_baseline": round(deg_hits / k, 3)},
                "burners_attributed": sorted(i["phone"] for i in self.inferences if ent_truth.get(i["attributed_to"]) == gt["kingpin"]),
                "cross_case_correct": f"{cc_ok}/{len(self.case_links)}", "roles": role_ok}
