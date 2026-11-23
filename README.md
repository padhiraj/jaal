# JAAL — Joint Analytics for Adversarial Links

An AI-powered criminal network analyser. JAAL takes FIRs, call detail records, telecom KYC, bank KYC and transfers, RTO/ANPR logs and social media. From these it:

- resolves aliases into real people (with explained, human-reviewable merges),
- builds one knowledge graph,
- ranks who matters (personalised PageRank, weighted betweenness, eigenvector and disruption impact inside a *network of interest*),
- detects burners, fake identities, structuring, mules, round-tripping, communication spikes, phones going dark and co-location,
- links "unrelated" FIRs across states.

Every score and link traces back to the exact source line. Every access goes into a hash-chained audit log, and access is controlled by role.

Zero dependencies: Python 3.9+ standard library and vanilla JS. Runs offline.

```bash
python3 run.py              # http://127.0.0.1:8765 (generates demo data on first run)
python3 run.py --evaluate   # score against hidden ground truth
python3 -m unittest discover -s tests -v
```

Results on the bundled synthetic "Operation Monsoon" dataset:

- The hidden kingpin, who is never named in any FIR, ranks **#2** (plain degree ranking puts him at #42).
- Precision@12 for syndicate members: **0.83** (degree baseline: 0.17).
- Both burner SIMs are attributed to him.
- **10/10** case pairs are correctly linked or kept separate.
- Entity-resolution F1: **0.97**, with precision 1.0.

The full explanation is in [docs/JAAL_Project_Report.pdf](docs/JAAL_Project_Report.pdf).

Demo users (selector at the top right): viewer (masked identifiers), analyst, supervisor (entity-resolution decisions + audit), auditor (audit only).
Deep links work too, for example `#select=P:vikram-rathod`, `#path=P:ramesh-yadav,P:venkat-rao` or `#user=tok-auditor&tab=audit`.

All data is fictional and machine-generated.

## Deploy online (Render, free)

1. Push this folder to a GitHub repository.
2. On render.com choose **New + → Blueprint** and pick the repository. `render.yaml` sets everything up.
   Alternatively, choose **New + → Web Service** and enter these settings by hand:
   - Runtime: Python 3
   - Build command: `echo "no dependencies"`
   - Start command: `python3 -u run.py --host 0.0.0.0 --port $PORT`
3. Share the `https://<name>.onrender.com` link.

Notes:
- The free tier sleeps when idle, so the first visit can take about a minute.
- Ingested FIRs and review decisions are lost when the service restarts. The demo data regenerates automatically.
- The demo users have no passwords. Only deploy fictional data this way.
