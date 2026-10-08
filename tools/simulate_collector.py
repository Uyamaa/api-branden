"""Pretends to be the collectors in the data centres, so the intake can be shown without real hardware.

    python tools/simulate_collector.py --url http://84.8.132.132:3000 --key YOUR_INGEST_API_KEY --dc "London DC-01"

Every few seconds it sends one SMART reading per drive. Most drives stay healthy. One drive ("-FAIL")
slowly heats up and collects bad sectors, which is what a failing drive looks like. The first time a
serial is seen the API registers the drive by itself. Stop with Ctrl+C. Uses only the standard library.
"""
import argparse
import json
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def send(url: str, key: str, readings: list[dict]) -> dict:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/ingest/readings", data=json.dumps({"readings": readings}).encode(),
        headers={"Content-Type": "application/json", "X-Ingest-Key": key}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        raise SystemExit(f"The server said {err.code}: {err.read().decode()[:300]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="the site, e.g. http://84.8.132.132:3000")
    ap.add_argument("--key", required=True, help="the server's INGEST_API_KEY")
    ap.add_argument("--dc", required=True, help="an existing data centre name, e.g. 'London DC-01'")
    ap.add_argument("--drives", type=int, default=5, help="how many healthy drives to simulate")
    ap.add_argument("--every", type=float, default=5.0, help="seconds between rounds")
    ap.add_argument("--rounds", type=int, default=0, help="stop after this many rounds (0 = until Ctrl+C)")
    args = ap.parse_args()

    random.seed()
    fleet = [{"serial": f"SIM-{i:04d}", "model": "Exos X18", "capacityTb": 18, "hours": random.randint(2000, 30000), "temp": random.uniform(32, 40)}
             for i in range(1, args.drives + 1)]
    fleet.append({"serial": "SIM-9999-FAIL", "model": "Ultrastar", "capacityTb": 16, "hours": 51000, "temp": 38.0, "bad": 0})

    round_no = 0
    while True:
        round_no += 1
        batch = []
        for d in fleet:
            d["hours"] += 1
            if "bad" in d:  # the failing drive gets worse every round
                d["temp"] = min(62, d["temp"] + random.uniform(0.3, 1.2))
                d["bad"] += random.randint(2, 9)
            else:
                d["temp"] = max(28, min(42, d["temp"] + random.uniform(-0.6, 0.6)))
            bad = d.get("bad", 0)
            batch.append({
                "serial": d["serial"], "model": d["model"], "capacityTb": d["capacityTb"], "dc": args.dc,
                "collectedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "temperature": round(d["temp"], 1), "powerOnHours": d["hours"],
                "reallocatedSectors": bad, "currentPendingSector": bad // 4, "offlineUncorrectable": bad // 6,
                "reportedUncorrectable": bad // 5,
            })
        result = send(args.url, args.key, batch)
        print(f"round {round_no}: sent {result['received']}, accepted {result['accepted']}, "
              f"new drives {result['registered'] or '-'}, rejected {len(result['rejected'])}")
        for bad_one in result["rejected"]:
            print("   rejected:", bad_one)
        if args.rounds and round_no >= args.rounds:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
