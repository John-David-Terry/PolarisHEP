import csv
import time
from pathlib import Path
import requests

MANIFEST = "top200_manifest_fixed.csv"
OUTDIR = Path("data/arxiv_pdfs/25808")
LOGDIR = Path("data/arxiv_pdfs/logs")
OUTDIR.mkdir(parents=True, exist_ok=True)
LOGDIR.mkdir(parents=True, exist_ok=True)

SUCCESS = []
FAIL = []

def clean_arxiv_id(x: str) -> str:
    x = (x or "").strip()
    x = x.replace("arxiv:", "").replace("ARXIV:", "").strip()
    # strip version if present (arXiv serves latest if omitted)
    if "v" in x and x.split("v")[-1].isdigit():
        base = x.rsplit("v", 1)[0]
        if base:
            x = base
    return x

with open(MANIFEST, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    cn = row["cn"].strip()
    arxiv_id = clean_arxiv_id(row.get("arxiv_id", ""))
    out_path = OUTDIR / f"{cn}.pdf"

    if not arxiv_id:
        FAIL.append((cn, "", "NO_ARXIV_ID"))
        continue

    if out_path.exists():
        SUCCESS.append((cn, arxiv_id, "ALREADY_EXISTS"))
        continue

    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    try:
        r = requests.get(url, timeout=45, headers={"User-Agent": "Polaris/1.0"})
        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code == 200 and "application/pdf" in ctype:
            out_path.write_bytes(r.content)
            SUCCESS.append((cn, arxiv_id, "DOWNLOADED"))
        else:
            FAIL.append((cn, arxiv_id, f"HTTP_{r.status_code}_CTYPE_{ctype}"))
    except Exception as e:
        FAIL.append((cn, arxiv_id, f"EXC_{type(e).__name__}:{e}"))

    time.sleep(1)  # be polite + deterministic

# write logs
with open(LOGDIR / "arxiv_success.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["cn", "arxiv_id", "status"])
    w.writerows(SUCCESS)

with open(LOGDIR / "arxiv_fail.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["cn", "arxiv_id", "error"])
    w.writerows(FAIL)

print(f"Done. success={len(SUCCESS)} fail={len(FAIL)}")
print(f"PDF dir: {OUTDIR}")
print(f"Logs: {LOGDIR/'arxiv_success.csv'} and {LOGDIR/'arxiv_fail.csv'}")
