"""Run with:  python3 -m unittest discover -s tests -v"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jaal import algorithms as alg  # noqa: E402
from jaal.audit import AuditLog, mask  # noqa: E402
from jaal.datagen import generate  # noqa: E402
from jaal.engine import Engine  # noqa: E402
from jaal.ingest import PersonRecord, norm_phone  # noqa: E402
from jaal.resolve import name_similarity, resolve  # noqa: E402


class Algorithms(unittest.TestCase):
    def test_betweenness_path(self):
        g = {"a": {"b": 1}, "b": {"a": 1, "c": 1}, "c": {"b": 1, "d": 1}, "d": {"c": 1}}
        bc = alg.betweenness(g)
        self.assertGreater(bc["b"], bc["a"])
        self.assertAlmostEqual(bc["b"], bc["c"])
        self.assertEqual(alg.betweenness(g, weighted=True)["b"], bc["b"])

    def test_bridge_has_max_betweenness(self):
        g = {}
        def e(a, b):
            g.setdefault(a, {})[b] = 1
            g.setdefault(b, {})[a] = 1
        for x, y in [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, "k"), ("k", 4)]:
            e(x, y)
        bc = alg.betweenness(g)
        self.assertEqual(max(bc, key=bc.get), "k")

    def test_louvain_two_cliques(self):
        g = {}
        def e(a, b):
            g.setdefault(a, {})[b] = 1
            g.setdefault(b, {})[a] = 1
        for c in (0, 10):
            for i in range(4):
                for j in range(i + 1, 4):
                    e(c + i, c + j)
        e(0, 10)
        part = alg.louvain(g)
        self.assertEqual(len(set(part.values())), 2)
        self.assertGreater(alg.modularity(g, part), 0.3)

    def test_cycles(self):
        d = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "x": {"a"}}
        cyc = alg.simple_cycles(d)
        self.assertEqual(len(cyc), 1)
        self.assertEqual(set(cyc[0]), {"a", "b", "c"})


class Resolution(unittest.TestCase):
    def test_phone_normalisation(self):
        for raw in ("+91 98200 11457", "09820011457", "919820011457", "98200-11457"):
            self.assertEqual(norm_phone(raw), "9820011457")
        self.assertIsNone(norm_phone("12345"))

    def test_transliteration(self):
        self.assertGreater(name_similarity("Imraan Shaikh", "Imran Sheikh"), 0.95)
        self.assertGreater(name_similarity("Suresh Pilai", "SURESH PILLAI"), 0.95)
        self.assertLess(name_similarity("Meera Rathod", "Vikram Rathod"), 0.95)  # same surname, different person

    def test_namesakes_not_merged(self):
        a = PersonRecord(rid="kyc:1", source="kyc", name="Amit Kumar", dob="1980-01-01", city="Delhi", phones=["9000000001"])
        b = PersonRecord(rid="kyc:2", source="kyc", name="Amit Kumar", dob="1992-05-05", city="Kolkata", phones=["9000000002"])
        ents, merges, review, _ = resolve([a, b])
        self.assertEqual(len(ents), 2)

    def test_shared_phone_merges(self):
        a = PersonRecord(rid="fir:X:Imraan Shaikh", source="fir", name="Imraan Shaikh", phones=["9820011457"], age=37)
        b = PersonRecord(rid="sub:9820011457", source="subscriber", name="IMRAN SHEIKH", phones=["9820011457"])
        ents, merges, _, _ = resolve([a, b])
        self.assertEqual(len(ents), 1)
        self.assertTrue(any("shared phone" in w for w in merges[0]["why"]))


class Audit(unittest.TestCase):
    def test_tamper_detection(self):
        d = Path(tempfile.mkdtemp())
        log = AuditLog(d / "a.jsonl")
        for i in range(5):
            log.append({"user": "u", "role": "analyst"}, "entity.read", f"P:{i}")
        self.assertTrue(log.verify()["ok"])
        lines = (d / "a.jsonl").read_text().splitlines()
        e = json.loads(lines[2])
        e["target"] = "P:someone-else"
        lines[2] = json.dumps(e)
        (d / "a.jsonl").write_text("\n".join(lines) + "\n")
        v = log.verify()
        self.assertFalse(v["ok"])
        self.assertEqual(v["broken_at"], 3)
        shutil.rmtree(d)

    def test_masking(self):
        m = mask({"id": "PH:9820011457", "label": "call 9820011457 a/c 50100234567812 car MH02AB7777"})
        self.assertNotIn("9820011457", json.dumps(m))
        self.assertNotIn("50100234567812", json.dumps(m))
        self.assertNotIn("MH02AB7777", json.dumps(m))


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        generate(cls.dir / "raw")
        cls.E = Engine(cls.dir / "raw", cls.dir / "state")
        cls.ev = cls.E.evaluate()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_entity_resolution_quality(self):
        self.assertEqual(self.ev["er"]["pairs_precision"], 1.0)
        self.assertGreater(self.ev["er"]["pairs_recall"], 0.9)

    def test_hidden_kingpin_found(self):
        self.assertLessEqual(self.ev["kingpin_rank"]["jaal_priority"], 3)
        self.assertGreater(self.ev["kingpin_rank"]["degree_only"], 20)
        self.assertEqual(self.ev["roles"]["Vikram Rathod"]["predicted"], "Suspected orchestrator")

    def test_burners_attributed(self):
        self.assertEqual(len(self.ev["burners_attributed"]), 2)

    def test_decoy_suppressed(self):
        self.assertEqual(self.ev["decoy_rank"]["degree_only"], 1)
        self.assertGreater(self.ev["decoy_rank"]["jaal_priority"], 12)

    def test_cross_case(self):
        self.assertEqual(self.ev["cross_case_correct"], "10/10")

    def test_every_alert_and_link_has_evidence(self):
        for a in self.E.alerts:
            if a["type"] not in ("dark",):
                self.assertTrue(a["evidence"], a["title"])
        for e in self.E.pg_edges.values():
            self.assertTrue(e["kg_edges"])

    def test_financial_patterns(self):
        types = {a["type"] for a in self.E.alerts}
        self.assertTrue({"structuring", "mule", "cycle", "rotation", "colocation"} <= types)

    def test_ingest_links_new_case(self):
        text = (ROOT / "web" / "app.js").read_text().split("const SAMPLE_FIR = `")[1].split("`;")[0]
        r = self.E.ingest_fir(text)
        strong = [l for l in r["links"] if l["strength"] == "strong"]
        self.assertTrue(strong and "Vikram Rathod" in strong[0]["path_labels"])


if __name__ == "__main__":
    unittest.main()
