import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import consumer as c  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402

KEY = "stream-test-key"


class Msg:
    def __init__(self, value, key=b"k", offset=0):
        self._v = value if isinstance(value, (bytes, type(None))) else json.dumps(value).encode()
        self._k, self._o = key, offset

    value = lambda self: self._v          # noqa: E731
    key = lambda self: self._k            # noqa: E731
    error = lambda self: None             # noqa: E731
    topic = lambda self: "smart.readings"  # noqa: E731
    partition = lambda self: 0            # noqa: E731
    offset = lambda self: self._o         # noqa: E731


class FakeConsumer:
    """Hands out the given batches one at a time, then asks the loop to stop."""
    def __init__(self, batches, stop):
        self.batches, self.stop, self.commits = list(batches), stop, 0

    def consume(self, num_messages, timeout):
        if not self.batches:
            self.stop["now"] = True
            return []
        return self.batches.pop(0)

    def commit(self, asynchronous=False):
        self.commits += 1


class FakeDLQ:
    def __init__(self):
        self.sent = []

    def produce(self, topic, key=None, value=None):
        self.sent.append((topic, json.loads(value)))

    def flush(self, _t):
        pass


def reading(serial="STR-0001", **over):
    base = {"serial": serial, "model": "Exos X18", "capacityTb": 18, "dc": "London DC-01",
            "temperature": 36, "powerOnHours": 1200}
    base.update(over)
    return base


def api_post(client):
    """Deliver through the real API app instead of HTTP."""
    def post(readings):
        r = client.post("/api/ingest/readings", json={"readings": readings}, headers={"X-Ingest-Key": KEY})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403, 408, 429) or r.status_code >= 500:
            raise c.Retryable(str(r.status_code))
        raise c.Poison(str(r.status_code))
    return post


def go(batches, post, tmp_path, sleep=lambda s: None):
    stop = {"now": False}
    cons, dlq = FakeConsumer(batches, stop), FakeDLQ()
    s = c.Settings(heartbeat_file=str(tmp_path / "hb"), batch_wait=0)
    c.run(cons, dlq, post, s, should_stop=lambda: stop["now"], sleep=sleep)
    return cons, dlq


@pytest.fixture()
def feed(client, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", KEY)
    return client


def count_readings(serial):
    db = SessionLocal()
    try:
        drive = db.query(models.HardDrive).filter_by(serial_number=serial).first()
        return 0 if drive is None else db.query(models.SmartReading).filter_by(drive_id=drive.drive_id).count()
    finally:
        db.close()


def test_readings_flow_into_the_database_and_are_committed(feed, tmp_path):
    cons, dlq = go([[Msg(reading("STR-0001")), Msg(reading("STR-0002"))]], api_post(feed), tmp_path)
    assert count_readings("STR-0001") == 1 and count_readings("STR-0002") == 1
    assert cons.commits == 1 and dlq.sent == []


def test_replayed_messages_do_not_duplicate(feed, tmp_path):
    msg = reading("STR-0003", collectedAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    go([[Msg(msg)], [Msg(msg)]], api_post(feed), tmp_path)   # same reading delivered twice (e.g. after a crash)
    assert count_readings("STR-0003") == 1


def test_bad_messages_go_to_the_dead_letter_topic_and_the_good_ones_still_land(feed, tmp_path):
    batch = [Msg(b"not json at all"), Msg(reading("STR-0004")),
             Msg(reading("STR-0005", dc="Atlantis")), Msg(reading("STR-0006", temperature=900))]
    cons, dlq = go([batch], api_post(feed), tmp_path)
    assert count_readings("STR-0004") == 1
    reasons = [p["reason"] for _, p in dlq.sent]
    assert len(dlq.sent) == 3
    assert "not valid JSON" in reasons
    assert any("Atlantis" in r for r in reasons) and any("temperature" in r for r in reasons)
    assert all(topic == "smart.readings.dlq" for topic, _ in dlq.sent)
    assert cons.commits == 1   # the stream moved on past the poison messages


def test_api_outage_is_waited_out_without_committing_or_losing_data(feed, tmp_path):
    real = api_post(feed)
    calls = {"n": 0}

    def flaky(readings):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise c.Retryable("API down")
        return real(readings)

    waits = []
    cons, dlq = go([[Msg(reading("STR-0007"))]], flaky, tmp_path, sleep=waits.append)
    assert count_readings("STR-0007") == 1
    assert waits == [1.0, 2.0, 4.0]          # exponential back-off
    assert cons.commits == 1 and dlq.sent == []


def test_wrong_key_retries_instead_of_dropping_data(feed, tmp_path, monkeypatch):
    real = api_post(feed)
    monkeypatch.setenv("INGEST_API_KEY", "someone-changed-it")

    def fixed_after_two(readings, state={"n": 0}):
        state["n"] += 1
        if state["n"] == 3:
            monkeypatch.setenv("INGEST_API_KEY", KEY)   # operator fixes the key
        return real(readings)

    cons, dlq = go([[Msg(reading("STR-0008"))]], fixed_after_two, tmp_path)
    assert count_readings("STR-0008") == 1 and dlq.sent == []


def test_whole_request_refused_as_malformed_goes_to_dlq(tmp_path):
    def refuse(_):
        raise c.Poison("API answered 422")
    cons, dlq = go([[Msg(reading("X1")), Msg(reading("X2"))]], refuse, tmp_path)
    assert len(dlq.sent) == 2 and cons.commits == 1


def test_large_batches_are_split_to_the_api_limit(tmp_path):
    sizes = []

    def ok(readings):
        sizes.append(len(readings))
        return {"accepted": len(readings), "duplicates": 0, "registered": [], "rejected": []}

    msgs = [Msg(reading(f"B-{i}")) for i in range(1200)]
    go([msgs], ok, tmp_path)
    assert sizes == [500, 500, 200]


def test_stop_during_outage_leaves_batch_uncommitted(tmp_path):
    stop = {"now": False}
    cons, dlq = FakeConsumer([[Msg(reading())]], stop), FakeDLQ()
    s = c.Settings(heartbeat_file=str(tmp_path / "hb"), batch_wait=0)

    def down(_):
        raise c.Retryable("down")

    def sigterm(_s):
        stop["now"] = True    # SIGTERM arrives while we wait
    c.run(cons, dlq, down, s, should_stop=lambda: stop["now"], sleep=sigterm)
    assert cons.commits == 0   # replayed on next start
    assert (tmp_path / "hb").exists()
