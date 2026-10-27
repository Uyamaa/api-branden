"""Simulated collector that publishes SMART readings to Kafka (stand-in for the real collectors).

    python producer.py --bootstrap localhost:9094 --dc "London DC-01" --drives 5 --every 5

The message key is the serial number, so each drive's readings stay in order within one partition.
Same fleet as tools/simulate_collector.py: healthy drives plus one ("-FAIL") that degrades.
"""
import argparse
import json
import random
import time
from datetime import datetime, timezone


def new_fleet(drives: int) -> list[dict]:
    fleet = [{"serial": f"SIM-{i:04d}", "model": "Exos X18", "capacityTb": 18,
              "hours": random.randint(2000, 30000), "temp": random.uniform(32, 40)} for i in range(1, drives + 1)]
    fleet.append({"serial": "SIM-9999-FAIL", "model": "Ultrastar", "capacityTb": 16, "hours": 51000, "temp": 38.0, "bad": 0})
    return fleet


def next_round(fleet: list[dict], dc: str) -> list[dict]:
    batch = []
    for d in fleet:
        d["hours"] += 1
        if "bad" in d:
            d["temp"] = min(62, d["temp"] + random.uniform(0.3, 1.2))
            d["bad"] += random.randint(2, 9)
        else:
            d["temp"] = max(28, min(42, d["temp"] + random.uniform(-0.6, 0.6)))
        bad = d.get("bad", 0)
        batch.append({
            "serial": d["serial"], "model": d["model"], "capacityTb": d["capacityTb"], "dc": dc,
            "collectedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "temperature": round(d["temp"], 1), "powerOnHours": d["hours"],
            "reallocatedSectors": bad, "currentPendingSector": bad // 4,
            "offlineUncorrectable": bad // 6, "reportedUncorrectable": bad // 5,
        })
    return batch


def main():
    from confluent_kafka import Producer

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bootstrap", default="localhost:9094")
    ap.add_argument("--topic", default="smart.readings")
    ap.add_argument("--dc", required=True, help="an existing data centre name")
    ap.add_argument("--drives", type=int, default=5)
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--rounds", type=int, default=0, help="0 = until Ctrl+C")
    args = ap.parse_args()

    producer = Producer({"bootstrap.servers": args.bootstrap, "enable.idempotence": True, "acks": "all"})
    fleet, n = new_fleet(args.drives), 0
    while True:
        n += 1
        for r in next_round(fleet, args.dc):
            producer.produce(args.topic, key=r["serial"], value=json.dumps(r).encode())
        producer.flush(10)
        print(f"round {n}: published {len(fleet)} readings")
        if args.rounds and n >= args.rounds:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
