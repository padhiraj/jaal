#!/usr/bin/env python3
"""JAAL — Joint Analytics for Adversarial Links.

    python3 run.py                 generate demo data (if missing) and start the web app on :8765
    python3 run.py --regen         regenerate the synthetic dataset first
    python3 run.py --evaluate      print evaluation against the hidden ground truth and exit
    python3 run.py --port 9000     choose a port
"""
import argparse
import json
import os
import shutil
from pathlib import Path

from jaal.datagen import generate

ROOT = Path(__file__).resolve().parent
RAW, STATE = ROOT / "data" / "raw", ROOT / "data" / "state"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regen", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    # Cloud hosts (Render, Railway, Hugging Face...) pass the port in $PORT and need 0.0.0.0
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--host", default="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    a = ap.parse_args()
    if a.regen or not (RAW / "cdr.csv").exists():
        if RAW.exists():
            shutil.rmtree(RAW)
        if STATE.exists():
            shutil.rmtree(STATE)
        print("Generating synthetic dataset:", generate(RAW))
    if a.evaluate:
        from jaal.engine import Engine
        e = Engine(RAW, STATE)
        print(json.dumps(e.evaluate(), indent=2))
        return
    from jaal.server import serve
    serve(RAW, STATE, a.host, a.port)


if __name__ == "__main__":
    main()
