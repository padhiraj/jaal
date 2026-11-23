"""Synthetic multi-source investigative dataset ("Operation Monsoon").

Generates deliberately messy, multi-jurisdiction data for a hidden inter-state
syndicate buried inside a civilian population:

  firs/*.txt          unstructured First Information Reports (3 linked + 2 unrelated)
  subscribers.csv     telecom subscriber registry (CAF/KYC of SIM cards)
  cdr.csv             call detail records with caller handset IMEI
  bank_kyc.csv        bank account KYC (individuals + shell companies)
  transactions.csv    fund transfers
  rto.csv             vehicle registrations
  anpr.csv            automatic number-plate recognition camera hits
  social.json         social-media handles + messages
  ground_truth.json   hidden truth used ONLY for evaluation, never by the engine

Names, phone formats and IDs are intentionally inconsistent across sources
(Imran Sheikh / Imraan Shaikh / Md. Imran Sheikh; +91 98200 11457 / 09820011457 ...).
Everything is fictional and seeded, so runs are reproducible.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import random
from pathlib import Path

START = dt.datetime(2026, 8, 1)
DAYS = 60
SEED = 1337


def day(d: int, h: int = 0, m: int = 0) -> dt.datetime:
    return START + dt.timedelta(days=d, hours=h, minutes=m)


def D(s: str) -> int:
    """'09-12' -> day offset from START."""
    mo, dd = map(int, s.split("-"))
    return (dt.datetime(2026, mo, dd) - START).days


class Gen:
    def __init__(self, seed: int = SEED):
        self.r = random.Random(seed)
        self.used_numbers: set[str] = set()
        self.truth_records: dict[str, str] = {}
        self.people: dict[str, dict] = {}

    # ---------- helpers ----------
    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.r.random()
            if p < L:
                return k
            k += 1

    def num(self, prefix: str = "") -> str:
        while True:
            n = prefix + "".join(self.r.choice("0123456789") for _ in range(10 - len(prefix)))
            if prefix == "":
                n = self.r.choice("6789") + n[1:]
            if n not in self.used_numbers:
                self.used_numbers.add(n)
                return n

    def imei(self) -> str:
        return "35" + "".join(self.r.choice("0123456789") for _ in range(13))

    def acct(self) -> str:
        return "".join(self.r.choice("0123456789") for _ in range(self.r.choice([11, 12, 14])))

    def pan(self, last: str) -> str:
        L = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        return "".join(self.r.choice(L) for _ in range(3)) + "P" + last[0].upper() + \
            "".join(self.r.choice("0123456789") for _ in range(4)) + self.r.choice(L)

    def fmt_phone(self, n: str, style: str) -> str:
        return {
            "plain": n,
            "intl": "+91 " + n[:5] + " " + n[5:],
            "dash": "+91-" + n,
            "zero": "0" + n,
            "cc": "91" + n,
            "split": n[:5] + " " + n[5:],
            "hyph": n[:5] + "-" + n[5:],
        }[style]


SYNDICATE = [
    # key, name, dob, city, role
    ("K", "Vikram Rathod", "1978-03-11", "Mumbai", "kingpin"),
    ("L1", "Imran Sheikh", "1989-06-02", "Mumbai", "lieutenant"),
    ("L2", "Suresh Pillai", "1985-01-20", "Hyderabad", "lieutenant"),
    ("F", "Anita Desai", "1981-11-30", "Kolkata", "financier"),
    ("M1", "Rahul Das", "1997-04-14", "Kolkata", "mule"),
    ("M2", "Pinky Roy", "1999-09-03", "Kolkata", "mule"),
    ("M3", "Abdul Karim", "1993-12-21", "Kolkata", "mule"),
    ("S1", "Ramesh Yadav", "1998-02-17", "Mumbai", "operative"),
    ("S2", "Sunny Kamble", "2000-07-08", "Mumbai", "operative"),
    ("S3", "Venkat Rao", "1994-05-25", "Hyderabad", "operative"),
    ("S4", "Kiran Goud", "1996-10-11", "Hyderabad", "operative"),
    ("C1", "Mahesh Naik", "1990-08-30", "Hyderabad", "courier"),
]
FIXED_PHONES = {
    "K": "9810045671", "L1": "9820011457", "L2": "9848022316", "F": "9830033874",
    "M1": "9831140021", "M2": "9831140588", "M3": "9831177203", "S1": "9867012345",
    "S2": "9867054321", "S3": "9849011223", "S4": "9849033445", "C1": "9849077889",
}
BURNER1, BURNER2 = "7004512390", "7004598812"

FIRST = ["Rajesh", "Priya", "Sneha", "Arjun", "Kavita", "Manoj", "Pooja", "Nikhil", "Ritu", "Sanjay",
         "Anjali", "Vivek", "Neha", "Rohan", "Swati", "Prakash", "Lakshmi", "Gopal", "Farah", "Harish",
         "Meena", "Tarun", "Shalini", "Naveen", "Divya", "Sameer", "Rekha", "Ashok", "Sunita", "Karthik",
         "Bhavna", "Imtiaz", "Joseph", "Lata", "Mohan", "Nisha", "Ravi", "Uma", "Vinod", "Zoya"]
LAST = {
    "Mumbai": ["Kulkarni", "Patil", "Joshi", "Deshmukh", "Mehta", "Shah", "Pawar", "Gupta"],
    "Hyderabad": ["Reddy", "Naidu", "Rao", "Chowdary", "Varma", "Ali", "Sastry", "Goud"],
    "Kolkata": ["Banerjee", "Chatterjee", "Ghosh", "Sen", "Mukherjee", "Bose", "Dutta", "Saha"],
    "Pune": ["Shinde", "Jadhav", "Bhosale", "Gaikwad", "Kale", "More", "Wagh", "Salunkhe"],
    "Delhi": ["Malhotra", "Kapoor", "Arora", "Chopra", "Bansal", "Saxena", "Khanna", "Sethi"],
}
CITY_COUNTS = {"Mumbai": 22, "Hyderabad": 12, "Kolkata": 12, "Pune": 10, "Delhi": 10}
FORCED_CIVILIANS = [  # name, city  (needed by FIR narratives / ER traps)
    ("Sneha Kulkarni", "Mumbai"), ("Deepak Verma", "Mumbai"), ("Meera Rathod", "Mumbai"),
    ("Ajay Shinde", "Pune"), ("Prakash Jadhav", "Pune"), ("Rohit Malhotra", "Delhi"),
    ("Sunita Arora", "Delhi"), ("Amit Kumar", "Delhi"), ("Amit Kumar", "Kolkata"),
    ("Farah Ali", "Hyderabad"),
]
TOWERS = {"Mumbai": ["MUM-AND-021", "MUM-KRL-007", "MUM-THN-044"], "Hyderabad": ["HYD-KPH-012", "HYD-SMS-003"],
          "Kolkata": ["KOL-SLT-018", "KOL-HWR-002"], "Pune": ["PUN-HDP-009"], "Delhi": ["DEL-LXN-031"]}
CAMERAS = {"Mumbai": ["Vashi Toll Plaza", "Panvel Junction"], "Hyderabad": ["Shamshabad Toll", "Kukatpally X-Road"],
           "Highway": ["Kurnool Toll NH44"], "Pune": ["Khed Shivapur Toll"], "Delhi": ["DND Flyway"],
           "Kolkata": ["Vidyasagar Setu"]}


def generate(out_dir: str | Path, seed: int = SEED) -> dict:
    g = Gen(seed)
    r = g.r
    out = Path(out_dir)
    (out / "firs").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ people
    people = g.people
    for key, name, dob, city, role in SYNDICATE:
        people[key] = dict(key=key, name=name, dob=dob, city=city, role=role,
                           phone=FIXED_PHONES[key], imei=g.imei(), pan=g.pan(name.split()[-1]))
        g.used_numbers.add(FIXED_PHONES[key])
    g.used_numbers |= {BURNER1, BURNER2}
    names_used = {p["name"] for p in people.values()} | {n for n, _ in FORCED_CIVILIANS}
    civ_idx = 0
    civilians: list[str] = []

    def add_civ(name, city):
        nonlocal civ_idx
        civ_idx += 1
        k = f"CV{civ_idx:02d}"
        y = r.randint(1960, 2002)
        people[k] = dict(key=k, name=name, dob=f"{y}-{r.randint(1,12):02d}-{r.randint(1,28):02d}", city=city,
                         role="civilian", phone=g.num(), imei=g.imei(), pan=g.pan(name.split()[-1]))
        civilians.append(k)
        return k

    for name, city in FORCED_CIVILIANS:
        add_civ(name, city)
    for city, n in CITY_COUNTS.items():
        have = sum(1 for k in civilians if people[k]["city"] == city)
        while have < n:
            nm = f"{r.choice(FIRST)} {r.choice(LAST[city])}"
            if nm in names_used:
                continue
            names_used.add(nm)
            add_civ(nm, city)
            have += 1
    by_name = {}
    for k in civilians:
        by_name.setdefault(people[k]["name"], k)
    civ_city = {c: [k for k in civilians if people[k]["city"] == c] for c in CITY_COUNTS}
    DECOY = by_name["Deepak Verma"]
    WIFE = by_name["Meera Rathod"]

    # family / friends of syndicate members
    family: dict[str, list[str]] = {}
    reserved = {by_name[n] for n in ("Deepak Verma", "Meera Rathod", "Ajay Shinde", "Prakash Jadhav",
                                     "Rohit Malhotra", "Sunita Arora", "Sneha Kulkarni")}
    pool = {c: [k for k in civ_city[c] if k not in reserved] for c in civ_city}
    for c in pool:
        r.shuffle(pool[c])
    for key, *_ in SYNDICATE:
        c = people[key]["city"]
        n = 3 if key == "K" else r.choice([2, 2, 3])
        fam = [pool[c].pop() for _ in range(n - (1 if key == "K" else 0))]
        if key == "K":
            fam = [WIFE] + fam
        family[key] = fam

    # victims (Mumbai civilians not already family)
    victims = [by_name["Sneha Kulkarni"]] + [pool["Mumbai"].pop() for _ in range(5)]
    victims_aug = [pool["Mumbai"].pop(), pool["Pune"].pop(), pool["Delhi"].pop()]

    # ------------------------------------------------------------------ call plan
    # (phone_a, phone_b, rate/day, first_day, last_day, kind, spike_group)
    plan: list[tuple] = []
    P = lambda k: people[k]["phone"]
    aug_end, sep_start = D("08-31"), D("09-01")
    for burner, a, b in [(BURNER1, 0, aug_end), (BURNER2, sep_start, DAYS - 1)]:
        plan += [(burner, P("L1"), 1.1, a, b, "op", "K"), (burner, P("L2"), 0.9, a, b, "op", "K"),
                 (burner, P("F"), 0.6, a, b, "op", "K")]
    plan += [(P("L1"), P("S1"), 1.8, 0, DAYS - 1, "op", "MUM"), (P("L1"), P("S2"), 1.5, 0, DAYS - 1, "op", "MUM"),
             (P("S1"), P("S2"), 1.0, 0, DAYS - 1, "op", "MUM"), (P("L1"), P("F"), 0.25, 0, DAYS - 1, "op", "KOL"),
             (P("L2"), P("S3"), 1.8, 0, D("09-05"), "op", "HYD"), (P("L2"), P("S4"), 1.4, 0, D("09-05"), "op", "HYD"),
             (P("L2"), P("C1"), 1.0, 0, DAYS - 1, "op", "HYD"), (P("S3"), P("S4"), 1.0, 0, D("09-05"), "op", "HYD"),
             (P("C1"), P("S3"), 0.5, 0, D("09-05"), "op", "HYD"),
             (P("F"), P("M1"), 0.8, 0, D("09-20"), "op", "KOL"), (P("F"), P("M2"), 0.7, 0, DAYS - 1, "op", "KOL"),
             (P("F"), P("M3"), 0.6, 0, DAYS - 1, "op", "KOL"), (P("M1"), P("M3"), 0.4, 0, D("09-20"), "op", "KOL")]
    for key, fam in family.items():
        end = D("09-05") if key in ("S3", "S4") else (D("09-20") if key == "M1" else DAYS - 1)
        for f in fam:
            plan.append((P(key), P(f), r.uniform(0.6, 1.4), 0, end, "fam", None))
    # civilian background: ring + random within city, a few cross-city
    civ_edges = set()
    for c, ks in civ_city.items():
        ks = [k for k in ks if k != DECOY]
        for i, k in enumerate(ks):
            civ_edges.add(tuple(sorted((k, ks[(i + 1) % len(ks)]))))
            for _ in range(2):
                o = r.choice(ks)
                if o != k:
                    civ_edges.add(tuple(sorted((k, o))))
    allc = [k for k in civilians if k != DECOY]
    for _ in range(4):
        a, b = r.sample(allc, 2)
        if people[a]["city"] != people[b]["city"]:
            civ_edges.add(tuple(sorted((a, b))))
    for a, b in sorted(civ_edges):
        plan.append((P(a), P(b), r.uniform(0.15, 0.7), 0, DAYS - 1, "civ", None))
    # decoy: delivery agent with the highest degree in the whole dataset
    for k in r.sample(allc, 30) + ["S1", "M1"]:
        plan.append((P(DECOY), P(k), 0.12, 0, DAYS - 1, "decoy", None))

    spikes = {"MUM": {D("09-10"), D("09-11")}, "HYD": {D("09-03"), D("09-04")},
              "KOL": {D("09-18"), D("09-19")}, "K": {D("09-03"), D("09-10"), D("09-18")}}
    cdr = []
    imei_of = {people[k]["phone"]: people[k]["imei"] for k in people}
    K_HANDSET = g.imei()
    imei_of[BURNER1] = imei_of[BURNER2] = K_HANDSET

    def add_call(t, a, b, kind, imei_override=None):
        dur = {"op": r.randint(20, 140), "fam": r.randint(60, 900), "civ": r.randint(30, 600),
               "decoy": r.randint(10, 50), "fraud": r.randint(120, 600)}[kind]
        cdr.append(dict(ts=t, caller=a, callee=b, dur=dur, imei=imei_override or imei_of.get(a, g.imei())))

    for a, b, rate, d0, d1, kind, grp in plan:
        for d in range(d0, d1 + 1):
            mult = 4.0 if grp and d in spikes.get(grp, ()) else 1.0
            if grp == "K" and d in spikes["K"]:
                mult = 3.0
            for _ in range(g.poisson(rate * mult)):
                t = day(d, r.randint(7, 23), r.randint(0, 59))
                if r.random() < (0.7 if a in (BURNER1, BURNER2) else 0.5):
                    add_call(t, a, b, kind)
                else:
                    add_call(t, b, a, kind)
    # the slip: kingpin's own SIM briefly used inside the burner handset
    for d in (D("08-03"), D("09-07")):
        add_call(day(d, 8, 12), P("K"), P(WIFE), "fam", imei_override=K_HANDSET)
        add_call(day(d, 8, 41), P("K"), P(family["K"][1]), "fam", imei_override=K_HANDSET)
    # fraud calls to victims
    for i, v in enumerate(victims):
        caller = P("S1") if i % 2 == 0 else P("S2")
        for _ in range(r.randint(2, 4)):
            add_call(day(D("09-08") + r.randint(0, 4), r.randint(10, 18), r.randint(0, 59)), caller, P(v), "fraud")
    for i, v in enumerate(victims_aug):
        add_call(day(D("08-15") + i, 11, r.randint(0, 59)), P("S1"), P(v), "fraud")
        add_call(day(D("08-15") + i, 11, r.randint(0, 59)), P("S1"), P(v), "fraud")
    cdr.sort(key=lambda c: c["ts"])

    # ------------------------------------------------------------------ subscribers
    subs = []
    for k, p in people.items():
        n = p["name"]
        shown = n.upper()
        id_type, id_no = "PAN", p["pan"]
        if k == "L1":
            shown = "IMRAN SHEIKH"
        if p["role"] == "civilian" and r.random() < 0.35:
            fn, ln = n.split()
            shown = f"{fn[0]}. {ln}".upper()
        if r.random() < 0.4 and k not in ("L1", "K"):
            id_type, id_no = "AADHAAR", "XXXX-XXXX-" + "".join(r.choice("0123456789") for _ in range(4))
        subs.append(dict(msisdn=g.fmt_phone(p["phone"], r.choice(["cc", "plain"])), subscriber_name=shown,
                         id_type=id_type, id_number=id_no, address_city=p["city"],
                         activation_date=f"20{r.randint(15, 25)}-{r.randint(1,12):02d}-{r.randint(1,28):02d}"))
        g.truth_records[f"sub:{p['phone']}"] = k
    subs.append(dict(msisdn="91" + BURNER1, subscriber_name="RAMU PRASAD", id_type="AADHAAR",
                     id_number="XXXX-XXXX-4471", address_city="Thane", activation_date="2026-07-29"))
    subs.append(dict(msisdn=BURNER2, subscriber_name="SHANKAR LAL", id_type="AADHAAR",
                     id_number="XXXX-XXXX-9083", address_city="Bhiwandi", activation_date="2026-08-30"))
    g.truth_records[f"sub:{BURNER1}"] = "K"
    g.truth_records[f"sub:{BURNER2}"] = "K"
    r.shuffle(subs)

    # ------------------------------------------------------------------ bank KYC
    kyc = []
    acct_of: dict[str, str] = {}
    banks = ["HDFC Bank", "SBI", "ICICI Bank", "Axis Bank", "Bank of Baroda", "Kotak Mahindra"]

    def add_acct(owner_key, holder, holder_type="INDIVIDUAL", signatory="", phone=None, dob=None, pan=None, tag=None):
        a = g.acct()
        p = people.get(owner_key, {})
        kyc.append(dict(account_no=a, bank=r.choice(banks), holder_name=holder, holder_type=holder_type,
                        authorised_signatory=signatory,
                        dob=dob if dob is not None else p.get("dob", ""),
                        pan=pan if pan is not None else p.get("pan", ""),
                        phone=phone if phone is not None else g.fmt_phone(p["phone"], r.choice(["zero", "plain", "dash"])),
                        city=p.get("city", "")))
        acct_of[tag or owner_key] = a
        if holder_type == "INDIVIDUAL":
            g.truth_records[f"kyc:{a}"] = owner_key
        else:
            g.truth_records[f"kycsig:{a}"] = owner_key
        return a

    for k, p in people.items():
        if k == "L1":
            add_acct(k, "Md. Imran Sheikh")
        elif k == "L2":
            add_acct(k, "Suresh Pilai")
        elif k == "M2":
            add_acct(k, "Pinki Roy")
        else:
            add_acct(k, p["name"])
    add_acct("K", "Vikram Rathore", phone="", tag="K2")  # second account, same PAN, no phone
    add_acct("F", "Desai Trading Co", "COMPANY", "Anita Desai", dob="", pan="AAFCD" + "4021" + "K", tag="DESAI")
    add_acct("F", "Anaya Exports Pvt Ltd", "COMPANY", "A. Desai", dob="", pan="AAGCA" + "7719" + "M", tag="ANAYA")

    # ------------------------------------------------------------------ transactions
    tx = []
    A = acct_of

    def t_(when, a, b, amt, mode="IMPS", nar=""):
        tx.append(dict(ts=when, src=A[a], dst=A[b], amount=int(amt), mode=mode, narration=nar))

    # civilian background
    for a, b in sorted(civ_edges):
        for d in range(DAYS):
            if r.random() < 0.035:
                t_(day(d, r.randint(8, 22), r.randint(0, 59)), *(r.sample([a, b], 2)), r.randint(5, 250) * 100, "UPI", "")
    for key, fam in family.items():
        for f in fam:
            for d in range(DAYS):
                if r.random() < 0.04:
                    t_(day(d, r.randint(8, 22)), key, f, r.randint(10, 300) * 100, "UPI", "family")

    def structure(src, dst, total, d, nar="trf"):
        left, t = total, day(d, 10)
        while left > 0:
            amt = min(left, r.randint(450, 499) * 100)
            t += dt.timedelta(minutes=r.randint(20, 160))
            t_(t, src, dst, amt, "IMPS", nar)
            left -= amt

    aug_amts, sep_amts = [62000, 88000, 54000], [340000, 185000, 260000, 120000, 410000, 95000]
    for i, (v, amt) in enumerate(zip(victims_aug, aug_amts)):
        t_(day(D("08-15") + i, 12, 30), v, "M1", amt, "IMPS", "KYC update fee")
    for i, (v, amt) in enumerate(zip(victims, sep_amts)):
        mule = "M1" if i % 2 == 0 else "M2"
        t_(day(D("09-09") + i // 2, 13, 10 + i), v, mule, amt, "IMPS", "KYC verification refund")
    structure("M1", "DESAI", 150000, D("08-17"))
    structure("M1", "DESAI", 600000, D("09-12"))
    structure("M2", "DESAI", 350000, D("09-12"))
    structure("M1", "M3", 140000, D("09-13"))
    structure("M3", "ANAYA", 130000, D("09-14"))
    for d, amt in [(D("08-19"), 450000), (D("09-13"), 900000), (D("09-16"), 700000)]:
        t_(day(d, 15), "DESAI", "ANAYA", amt, "RTGS", "export advance")
    for d, amt, dst in [(D("08-21"), 800000, "K"), (D("09-15"), 850000, "K"), (D("09-17"), 500000, "K2")]:
        t_(day(d, 16), "ANAYA", dst, amt, "RTGS", "consultancy fee")
    t_(day(D("09-22"), 11), "K2", "DESAI", 450000, "RTGS", "loan repayment")  # round trip
    t_(day(D("09-23"), 11), "M3", "DESAI", 48000, "IMPS", "")
    for d in (D("08-10"), D("08-28"), D("09-15")):
        t_(day(d, 14), "ANAYA", "L2", r.randint(20, 30) * 10000, "NEFT", "freight charges")
        for s in ("S3", "S4", "C1"):
            if d < D("09-05") or s == "C1":
                t_(day(d + 1, r.randint(9, 20)), "L2", s, r.randint(15, 25) * 1000, "UPI", "")
    for d in (D("08-14"), D("09-14")):
        t_(day(d, 12), "DESAI", "L1", 150000, "NEFT", "commission")
        for s in ("S1", "S2"):
            t_(day(d + 1, 18), "L1", s, r.randint(20, 40) * 1000, "UPI", "")
    for d in (D("08-01"), D("09-01")):
        t_(day(d, 10), "DESAI", "F", 100000, "NEFT", "salary")
    tx.sort(key=lambda x: x["ts"])

    # ------------------------------------------------------------------ vehicles + ANPR
    rto, anpr = [], []
    veh = {"L2": "TS09EK4521", "C1": "AP28CX9911", "L1": "MH02AB7777"}
    for k, reg in veh.items():
        nm = {"L1": "Imran Shaikh", "L2": "Suresh Pillai", "C1": "Mahesh Naik"}[k]
        rto.append(dict(reg_no=reg, owner_name=nm, owner_phone=g.fmt_phone(P(k), "plain"), city=people[k]["city"],
                        model=r.choice(["Maruti Eeco", "Toyota Innova", "Mahindra Bolero"])))
        g.truth_records[f"rto:{reg}"] = k
    state = {"Mumbai": "MH", "Hyderabad": "TS", "Kolkata": "WB", "Pune": "MH", "Delhi": "DL"}
    civ_veh = r.sample(civilians, 14)
    for k in civ_veh:
        p = people[k]
        reg = f"{state[p['city']]}{r.randint(1, 40):02d}{r.choice('ABCDEFGHJK')}{r.choice('ABCDEFGHJK')}{r.randint(1000, 9999)}"
        veh[k] = reg
        rto.append(dict(reg_no=reg, owner_name=p["name"], owner_phone=g.fmt_phone(p["phone"], "plain"),
                        city=p["city"], model=r.choice(["Hyundai i20", "Honda City", "Maruti Swift", "Tata Nexon"])))
        g.truth_records[f"rto:{reg}"] = k
    for k, reg in veh.items():
        c = people[k]["city"]
        cams = CAMERAS.get(c, []) + (CAMERAS["Highway"] if c == "Hyderabad" else [])
        for d in range(DAYS):
            if r.random() < 0.3:
                anpr.append(dict(ts=day(d, r.randint(6, 23), r.randint(0, 59)), reg_no=reg, camera=r.choice(cams)))
    for d in (D("08-12"), D("08-29"), D("09-03")):
        t0 = day(d, 2, r.randint(0, 40))
        anpr.append(dict(ts=t0, reg_no=veh["L2"], camera="Kurnool Toll NH44"))
        anpr.append(dict(ts=t0 + dt.timedelta(minutes=r.randint(2, 8)), reg_no=veh["C1"], camera="Kurnool Toll NH44"))
    anpr.sort(key=lambda x: x["ts"])

    # ------------------------------------------------------------------ social
    soc_accounts = [
        dict(handle="@bhaijaan_mumbai", display_name="Imran S.", bio="Mumbai | call 98200-11457", city="Mumbai"),
        dict(handle="@sp_hyd", display_name="Suresh.P", bio="Hyderabad transport business 9848022316", city="Hyderabad"),
        dict(handle="@rammy_007", display_name="Rammy", bio="Mumbai ki galli", city="Mumbai"),
        dict(handle="@chinna_hyd", display_name="Chinna", bio="", city="Hyderabad"),
    ]
    truth_soc = {"@bhaijaan_mumbai": "L1", "@sp_hyd": "L2", "@rammy_007": "S1", "@chinna_hyd": "S3"}
    for k in r.sample(civilians, 12):
        p = people[k]
        h = "@" + p["name"].lower().replace(" ", "_") + str(r.randint(1, 99))
        soc_accounts.append(dict(handle=h, display_name=p["name"], bio=f"{p['city']} | {p['phone']}", city=p["city"]))
        truth_soc[h] = k
    for h, k in truth_soc.items():
        g.truth_records[f"soc:{h}"] = k
    msgs = []
    lines = ["maal ready hai?", "seth bola kal tak", "parcel nikal gaya", "account change karo", "5 peti aaya",
             "naya number save karo", "kal Kurnool pe milte", "sab clear", "list bhejo", "police ka scene hai, ruk"]
    gang = ["@bhaijaan_mumbai", "@sp_hyd", "@rammy_007", "@chinna_hyd"]
    for d in range(0, DAYS, 2):
        for _ in range(r.randint(0, 2)):
            a = r.choice(gang)
            msgs.append(dict(ts=day(d, r.randint(9, 23)), sender=a, to="#monsoon_deals", text=r.choice(lines)))
    civ_h = [s["handle"] for s in soc_accounts[4:]]
    for _ in range(60):
        a, b = r.sample(civ_h, 2)
        msgs.append(dict(ts=day(r.randint(0, DAYS - 1), r.randint(8, 23)), sender=a, to=b,
                         text=r.choice(["hi", "call me", "dinner sunday?", "sent the photos", "happy bday!"])))
    msgs.sort(key=lambda m: m["ts"])

    # ------------------------------------------------------------------ FIRs
    def ph(k, style):
        return g.fmt_phone(P(k), style)

    V1 = people[victims[0]]
    firs = {}
    firs["FIR_MUM_2026_0457.txt"] = f"""FIRST INFORMATION REPORT (Under Section 173 BNSS)
FIR No: MUM/2026/0457    Police Station: Andheri East, Mumbai    District: Mumbai Suburban    State: Maharashtra
Date of Report: 2026-09-14    Date of Occurrence: 2026-09-09 to 2026-09-12
Sections: BNS 318(4), 319(2), 61(2); IT Act 66C, 66D

Complainant: {V1['name']}, aged {2026 - int(V1['dob'][:4])}, r/o Vile Parle East, Mumbai, Mob: {g.fmt_phone(V1['phone'], 'split')}

Brief facts:
The complainant states that on 2026-09-09 she received a call from mobile no. {ph('S1', 'intl')} from a person
posing as a bank KYC officer. On the pretext of a KYC update she was made to share an OTP and transfer
Rs. 3,40,000 to account no. {A['M1']} which was described as a "KYC verification refund" account.
Five similar complaints from Andheri, Kurla and Thane were received in the same week with the same modus operandi.
During investigation the caller was identified and arrested. The accused disclosed that the SIM-cards and
"customer lists" were supplied by one Imraan Shaikh @ Bhaijaan, who collects the proceeds for a person the
gang calls 'Seth'. The money was immediately split and moved onward in amounts below Rs. 50,000.

Accused:
1. Ramesh Yadav @ Rammy, s/o Shyamlal Yadav, aged 28, r/o Kurla West, Mumbai, Mob: {ph('S1', 'split')} (arrested)
2. Sunny Kamble, s/o Dattatray Kamble, aged 26, r/o Ghatkopar, Mumbai, Mob: {ph('S2', 'hyph')} (arrested)
3. Imraan Shaikh @ Bhaijaan, aged about 37, r/o Mumbra, Thane, Mob: {ph('L1', 'intl')} (absconding)

Property involved: Rs. 3,40,000 (complainant) + approx. Rs. 10,70,000 (linked complaints)
Investigating Officer: PSI A. Gawde
"""
    firs["FIR_HYD_2026_1182.txt"] = f"""FIRST INFORMATION REPORT
FIR No: HYD/2026/1182    Police Station: Shamshabad, Cyberabad    State: Telangana
Date of Report: 2026-09-05    Date of Occurrence: 2026-09-05
Sections: NDPS Act 20(b)(ii)(C), 29

Complainant: SI K. Srinivas, Shamshabad PS (suo motu)

Brief facts:
On credible information a Maruti Eeco bearing registration no. AP 28 CX 9911 was intercepted near Shamshabad
Toll at 03:40 hrs. 42 kg of dry ganja was recovered from concealed cavities. The two occupants were detained.
The driver of the vehicle fled on foot and has been identified as Mahesh Naik, Mob: {ph('C1', 'zero')}.
The detained persons revealed that the consignment was arranged by one Suresh Pilai of Kukatpally who
"runs a transport business", reachable on {ph('L2', 'split')}, and that payments come from "the Kolkata side".

Accused:
1. Venkat Rao @ Chinna, s/o Narsimha Rao, aged 32, r/o Kukatpally, Hyderabad, Mob: {ph('S3', 'plain')} (arrested)
2. Kiran Goud, s/o Ramulu Goud, aged 29, r/o Medchal, Hyderabad, Mob: {ph('S4', 'dash')} (arrested)
3. Mahesh Naik, aged 36, r/o Kurnool, Mob: {ph('C1', 'zero')} (absconding)
4. Suresh Pilai, aged about 41, r/o Kukatpally, Hyderabad, Mob: {ph('L2', 'split')} (absconding)

Property seized: 42 kg ganja, vehicle AP28CX9911, two mobile phones
Investigating Officer: Inspector M. Rafiq
"""
    firs["FIR_KOL_2026_0733.txt"] = f"""FIRST INFORMATION REPORT
FIR No: KOL/2026/0733    Police Station: Bidhannagar Cyber PS, Kolkata    State: West Bengal
Date of Report: 2026-09-21    Date of Occurrence: 2026-08-15 to 2026-09-20
Sections: BNS 318(4), 61(2); PMLA 3

Complainant: Branch Manager, on behalf of bank (suspicious transaction report)

Brief facts:
The bank flagged savings a/c no. {A['M1']} held by Rahul Das which received multiple credits from victims of
"KYC update" frauds in Maharashtra, Pune and Delhi, each followed within hours by debits in round amounts
of Rs. 45,000 - 49,900 to current a/c {A['DESAI']} (Desai Trading Co). A further a/c {A['M2']} in the name
of Pinki Roy shows the same pattern. Rahul Das was arrested on 2026-09-20 and admitted to "renting" his
account for a monthly commission paid in cash. He states his handler calls from {ph('F', 'split')}.

Accused:
1. Rahul Das, s/o Bimal Das, aged 29, r/o Salt Lake, Kolkata, Mob: {ph('M1', 'cc')} (arrested)
2. Pinki Roy, aged 27, r/o Howrah, Mob: {ph('M2', 'plain')} (notice issued)

Investigating Officer: Inspector S. Mitra
"""
    PUNA, PUNC = people[by_name["Ajay Shinde"]], people[by_name["Prakash Jadhav"]]
    firs["FIR_PUN_2026_0219.txt"] = f"""FIRST INFORMATION REPORT
FIR No: PUN/2026/0219    Police Station: Hadapsar, Pune    State: Maharashtra
Date of Report: 2026-08-26    Date of Occurrence: 2026-08-25
Sections: BNS 303(2)

Complainant: {PUNC['name']}, aged {2026 - int(PUNC['dob'][:4])}, r/o Hadapsar, Pune, Mob: {g.fmt_phone(PUNC['phone'], 'plain')}

Brief facts:
The complainant's motorcycle parked outside his shop was stolen at night. CCTV footage shows a known person
from the locality taking the vehicle. The motorcycle was recovered from a scrap dealer.

Accused:
1. Ajay Shinde, aged 24, r/o Hadapsar, Pune, Mob: {g.fmt_phone(PUNA['phone'], 'split')} (arrested)

Investigating Officer: PSI R. Kale
"""
    DA, DC = people[by_name["Rohit Malhotra"]], people[by_name["Sunita Arora"]]
    firs["FIR_DEL_2026_0904.txt"] = f"""FIRST INFORMATION REPORT
FIR No: DEL/2026/0904    Police Station: Laxmi Nagar, Delhi    State: Delhi
Date of Report: 2026-09-02    Date of Occurrence: 2026-09-01
Sections: BNS 115(2), 351(2)

Complainant: {DC['name']}, aged {2026 - int(DC['dob'][:4])}, r/o Laxmi Nagar, Delhi, Mob: {g.fmt_phone(DC['phone'], 'intl')}

Brief facts:
A parking dispute between neighbours escalated and the accused assaulted the complainant's son.

Accused:
1. Rohit Malhotra, aged 38, r/o Laxmi Nagar, Delhi, Mob: {g.fmt_phone(DA['phone'], 'plain')} (bailed)

Investigating Officer: SI P. Yadav
"""
    fir_truth = {
        "MUM/2026/0457": {V1["name"]: victims[0], "Ramesh Yadav": "S1", "Sunny Kamble": "S2", "Imraan Shaikh": "L1"},
        "HYD/2026/1182": {"Venkat Rao": "S3", "Kiran Goud": "S4", "Mahesh Naik": "C1", "Suresh Pilai": "L2"},
        "KOL/2026/0733": {"Rahul Das": "M1", "Pinki Roy": "M2"},
        "PUN/2026/0219": {PUNC["name"]: by_name["Prakash Jadhav"], "Ajay Shinde": by_name["Ajay Shinde"]},
        "DEL/2026/0904": {DC["name"]: by_name["Sunita Arora"], "Rohit Malhotra": by_name["Rohit Malhotra"]},
    }
    for fno, m in fir_truth.items():
        for nm, k in m.items():
            g.truth_records[f"fir:{fno}:{nm}"] = k

    # ------------------------------------------------------------------ write files
    def wcsv(name, rows, cols):
        with open(out / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow({c: (row[c].strftime("%Y-%m-%d %H:%M:%S") if isinstance(row[c], dt.datetime) else row[c])
                            for c in cols})

    for fn, txt in firs.items():
        (out / "firs" / fn).write_text(txt)
    wcsv("subscribers.csv", subs, ["msisdn", "subscriber_name", "id_type", "id_number", "address_city", "activation_date"])
    tower = lambda n: r.choice(TOWERS.get(next((people[k]["city"] for k in people if people[k]["phone"] == n), "Mumbai"),
                                          TOWERS["Mumbai"]))
    cdr_rows = [dict(call_id=f"C{i:06d}", timestamp=c["ts"], caller=g.fmt_phone(c["caller"], r.choice(["cc", "plain"])),
                     callee=g.fmt_phone(c["callee"], r.choice(["cc", "plain"])), duration_s=c["dur"],
                     caller_imei=c["imei"], cell_id=tower(c["caller"])) for i, c in enumerate(cdr)]
    wcsv("cdr.csv", cdr_rows, ["call_id", "timestamp", "caller", "callee", "duration_s", "caller_imei", "cell_id"])
    wcsv("bank_kyc.csv", kyc, ["account_no", "bank", "holder_name", "holder_type", "authorised_signatory", "dob", "pan",
                               "phone", "city"])
    wcsv("transactions.csv", [dict(txn_id=f"T{i:06d}", timestamp=t["ts"], from_account=t["src"], to_account=t["dst"],
                                   amount=t["amount"], mode=t["mode"], narration=t["narration"]) for i, t in enumerate(tx)],
         ["txn_id", "timestamp", "from_account", "to_account", "amount", "mode", "narration"])
    wcsv("rto.csv", rto, ["reg_no", "owner_name", "owner_phone", "city", "model"])
    wcsv("anpr.csv", [dict(hit_id=f"A{i:05d}", timestamp=a["ts"], reg_no=a["reg_no"], camera=a["camera"])
                      for i, a in enumerate(anpr)], ["hit_id", "timestamp", "reg_no", "camera"])
    (out / "social.json").write_text(json.dumps(dict(
        accounts=soc_accounts,
        messages=[dict(id=f"M{i:04d}", ts=m["ts"].strftime("%Y-%m-%d %H:%M:%S"), sender=m["sender"], to=m["to"],
                       text=m["text"]) for i, m in enumerate(msgs)]), indent=1))
    truth = dict(
        people={k: dict(name=p["name"], role=p["role"], city=p["city"]) for k, p in people.items()},
        syndicate=[k for k, *_ in SYNDICATE], kingpin="K", decoy=DECOY,
        burners=[BURNER1, BURNER2], records=g.truth_records,
        note="Used only by the evaluation harness. The analysis engine never reads this file.")
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=1))
    return dict(people=len(people), calls=len(cdr_rows), transactions=len(tx), anpr=len(anpr), firs=len(firs))


if __name__ == "__main__":
    import sys
    print(generate(sys.argv[1] if len(sys.argv) > 1 else "data/raw"))
