"""Kafka -> Fleet API bridge.

Reads SMART readings from a Kafka topic and delivers them to POST /api/ingest/readings.

Delivery guarantees
* At-least-once: offsets are committed only AFTER the API accepted the batch. If the consumer
  crashes in between, the batch is replayed. That is safe because the intake skips a reading it
  already has (same drive + collectedAt), so a replay creates no duplicates.
* Nothing is lost silently: a message that is not valid JSON, or that the API rejects (bad value,
  unknown data centre ...), is copied to the dead-letter topic with the reason, and the stream moves on.
* Outages are waited out: if the API is down or answers 5xx/429/401, the batch is retried with
  back-off and the offsets are NOT committed, so Kafka simply holds the data meanwhile.
* Per-drive order: the producer uses the serial number as the message key, so one drive's readings
  always land in one partition, in order.
"""
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("consumer")

MAX_BATCH = 500   # the intake accepts at most 500 readings per request


@dataclass
class Settings:
    bootstrap: str = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
    topic: str = os.getenv("READINGS_TOPIC", "smart.readings")
    dlq_topic: str = os.getenv("DLQ_TOPIC", "smart.readings.dlq")
    group: str = os.getenv("CONSUMER_GROUP", "fleet-ingest")
    api_url: str = os.getenv("API_URL", "http://api-branden:8000")
    api_key: str = os.getenv("INGEST_API_KEY", "")
    batch_size: int = int(os.getenv("BATCH_SIZE", "200"))
    batch_wait: float = float(os.getenv("BATCH_WAIT_SECONDS", "1.0"))
    heartbeat_file: str = os.getenv("HEARTBEAT_FILE", "/tmp/consumer-heartbeat")
    max_backoff: float = float(os.getenv("MAX_BACKOFF_SECONDS", "60"))


class Retryable(Exception):
    """The batch could not be delivered right now but may be later (API down, overloaded, key not set yet)."""


class Poison(Exception):
    """The API refused the whole request as malformed; retrying will never help."""


# ---------- delivery to the API ----------

def http_post(settings: Settings):
    """Returns post(readings) -> parsed JSON body. Raises Retryable / Poison."""
    import httpx

    def post(readings: list[dict]) -> dict:
        try:
            r = httpx.post(settings.api_url.rstrip("/") + "/api/ingest/readings", json={"readings": readings},
                           headers={"X-Ingest-Key": settings.api_key}, timeout=20)
        except httpx.HTTPError as exc:
            raise Retryable(f"API unreachable: {exc}") from exc
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403, 408, 429) or r.status_code >= 500:   # 401/503: key missing or wrong on one side
            raise Retryable(f"API answered {r.status_code}: {r.text[:200]}")
        raise Poison(f"API answered {r.status_code}: {r.text[:200]}")

    return post


# ---------- core logic (no Kafka imports, so it can be tested) ----------

def decode(msg) -> tuple[dict | None, str | None]:
    """(reading, None) or (None, reason)."""
    raw = msg.value()
    if raw is None:
        return None, "empty message"
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "not valid JSON"
    if not isinstance(data, dict):
        return None, "message is not a JSON object"
    return data, None


def process_batch(messages, post, send_to_dlq) -> dict:
    """Deliver one batch. Returns counts. Raises Retryable if nothing should be committed yet.

    send_to_dlq(msg, reason) is called for every message that cannot be delivered, ever.
    """
    counts = {"received": len(messages), "accepted": 0, "duplicates": 0, "dead": 0, "registered": 0}
    valid: list[tuple[object, dict]] = []
    for msg in messages:
        reading, why = decode(msg)
        if why:
            send_to_dlq(msg, why)
            counts["dead"] += 1
        else:
            valid.append((msg, reading))

    for start in range(0, len(valid), MAX_BATCH):
        chunk = valid[start:start + MAX_BATCH]
        try:
            result = post([reading for _, reading in chunk])
        except Poison as exc:
            for msg, _ in chunk:
                send_to_dlq(msg, str(exc))
            counts["dead"] += len(chunk)
            continue
        counts["accepted"] += result.get("accepted", 0)
        counts["duplicates"] += result.get("duplicates", 0)
        counts["registered"] += len(result.get("registered", []))
        for bad in result.get("rejected", []):
            msg, _ = chunk[bad["index"]]
            send_to_dlq(msg, bad.get("error", "rejected by API"))
            counts["dead"] += 1
    return counts


def run(consumer, dlq, post, settings: Settings, should_stop=lambda: False, sleep=time.sleep):
    """The main loop. `consumer` / `dlq` are confluent_kafka Consumer / Producer (or fakes)."""
    heartbeat = Path(settings.heartbeat_file)

    def to_dlq(msg, reason):
        log.warning("dead letter %s[%s]@%s: %s", msg.topic(), msg.partition(), msg.offset(), reason)
        payload = {"reason": reason, "topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset(),
                   "value": (msg.value() or b"").decode("utf-8", "replace")}
        dlq.produce(settings.dlq_topic, key=msg.key(), value=json.dumps(payload).encode())

    backoff = 1.0
    while not should_stop():
        heartbeat.touch()
        messages = [m for m in consumer.consume(num_messages=settings.batch_size, timeout=settings.batch_wait)
                    if _usable(m)]
        if not messages:
            continue
        while True:
            try:
                counts = process_batch(messages, post, to_dlq)
                break
            except Retryable as exc:
                log.error("%s; retrying in %.0fs (offsets not committed)", exc, backoff)
                heartbeat.touch()
                sleep(backoff)
                backoff = min(backoff * 2, settings.max_backoff)
                if should_stop():
                    return   # leave uncommitted: the batch is replayed on the next start
        backoff = 1.0
        dlq.flush(10)            # dead letters must be safely written BEFORE we commit past them
        consumer.commit(asynchronous=False)
        log.info("batch: %s", counts)


def _usable(msg) -> bool:
    err = msg.error()
    if err is None:
        return True
    log.error("kafka error: %s", err)
    return False


def main():
    from confluent_kafka import Consumer, Producer

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    s = Settings()
    if not s.api_key:
        raise SystemExit("INGEST_API_KEY is not set")
    consumer = Consumer({
        "bootstrap.servers": s.bootstrap, "group.id": s.group,
        "enable.auto.commit": False,          # we commit by hand, after delivery
        "auto.offset.reset": "earliest",      # a brand-new group starts from the oldest retained data
        "partition.assignment.strategy": "cooperative-sticky",
    })
    dlq = Producer({"bootstrap.servers": s.bootstrap, "enable.idempotence": True, "acks": "all"})
    consumer.subscribe([s.topic])

    stop = {"now": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.update(now=True))
    log.info("consuming %s from %s, delivering to %s", s.topic, s.bootstrap, s.api_url)
    try:
        run(consumer, dlq, http_post(s), s, should_stop=lambda: stop["now"])
    finally:
        consumer.close()   # leave the group cleanly so partitions are reassigned quickly
        dlq.flush(10)


if __name__ == "__main__":
    main()
