"""AI-path load test (§14 "Load": 30 concurrent interviews; follow-up and transcription request rates with
mock Kimi latency distribution; STT on the GPU node with audio-40).

Mode inprocess (default, no infrastructure): runs the real Processor + FOLLOWUP / ANSWER_ASSESSMENT handlers with
the worker's concurrency limits (realtime 4, standard 8 — A-09) against a mock model whose latency follows a
log-normal distribution. Measures queue wait + processing per job and checks the S-09 budgets.

Mode kafka (load-test environment): produces ihr.interview.followup.requested / ihr.speech.transcription.requested
at the same per-interview rate and measures request -> .completed / .failed on the result topics.

  python tools/loadtest.py --interviews 30 --turns 8 --answer-seconds 90
  python tools/loadtest.py --mode kafka --bootstrap kafka:9092 --interviews 30 --turns 8 --audio eval/datasets/audio-40
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs/py-worker-common/src", "services/ihr-ai-worker", "services/ihr-speech-service"):
    sys.path.insert(0, str(ROOT / p))
os.environ.setdefault("IHR_CONTRACTS_DIR", str(ROOT / "contracts"))
FIX = ROOT / "contracts/fixtures"

FOLLOWUP_P95_TARGET = 6.0      # §12.2 FollowupLatencyHigh / §13.3
FOLLOWUP_DEADLINE = 12.0       # §7.4


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))] if v else float("nan")


class LatencyMock:
    """MockLlmClient with log-normal latency (median, p95) per model slot."""

    def __init__(self, dist: dict[str, tuple[float, float]], seed: int = 7):
        from ihr_ai_worker.llm.mock import MockLlmClient
        self.inner = MockLlmClient()
        self.provider_name = "mock-load"
        self.dist, self.rng, self.lock = dist, random.Random(seed), threading.Lock()

    def model_for(self, slot):
        return self.inner.model_for(slot)

    def _sleep(self, slot):
        med, p95 = self.dist[slot]
        sigma = math.log(p95 / med) / 1.645
        with self.lock:
            s = self.rng.lognormvariate(math.log(med), sigma)
        time.sleep(s)

    def complete_json(self, **kw):
        self._sleep(kw["model_slot"])
        return self.inner.complete_json(**kw)


def inprocess(args) -> int:
    from ihr_worker_common.processor import Processor
    from ihr_worker_common.storage import InMemoryStorage, put_json
    from ihr_ai_worker.pipelines.answer_assessment import AnswerAssessmentHandler
    from ihr_ai_worker.pipelines.followup import FollowupHandler
    from ihr_ai_worker.prompts import load_registry

    reg = load_registry(ROOT / "docs/prompts/active.yaml")
    llm = LatencyMock({"realtime": (args.followup_median, args.followup_p95),
                       "standard": (args.assess_median, args.assess_p95)})
    storage = InMemoryStorage()
    transcript = json.loads((FIX / "speech/transcription/expected_output.json").read_text())
    fu_req = json.loads((FIX / "ai/followup_decision/request.json").read_text())
    aa_req = json.loads((FIX / "ai/answer_assessment/request.json").read_text())
    tref = put_json(storage, fu_req["payload"]["transcript"]["bucket"], fu_req["payload"]["transcript"]["key"], transcript)
    fu_proc = Processor(FollowupHandler(llm, reg, debug_capture_enabled=False), storage, "load", "load_fu", max_workers=4)
    aa_proc = Processor(AnswerAssessmentHandler(llm, reg, debug_capture_enabled=False), storage, "load", "load_aa",
                        max_workers=8)
    # the Kafka runner bounds in-flight jobs per deployment with a semaphore (A-09)
    fu_sem, aa_sem = threading.Semaphore(4), threading.Semaphore(8)
    results = {"FOLLOWUP": [], "ANSWER_ASSESSMENT": []}
    lock = threading.Lock()

    def submit(task, proc, sem, req):
        env = json.loads(json.dumps(req))
        env["eventId"] = str(uuid.uuid4())
        env["payload"]["jobId"] = str(uuid.uuid4())
        env["payload"]["transcript"] = tref
        t0 = time.monotonic()
        with sem:
            [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
        with lock:
            results[task].append((time.monotonic() - t0, out.topic.endswith(".completed")))

    def interview(i):
        rng = random.Random(i)
        time.sleep(rng.uniform(0, args.answer_seconds))            # interviews start staggered
        with ThreadPoolExecutor(2) as ex:
            for _ in range(args.turns):
                time.sleep(rng.uniform(0.6, 1.0) * args.answer_seconds)   # candidate answers
                ex.submit(submit, "ANSWER_ASSESSMENT", aa_proc, aa_sem, aa_req)
                submit("FOLLOWUP", fu_proc, fu_sem, fu_req)                  # the candidate waits for this one

    t0 = time.monotonic()
    with ThreadPoolExecutor(args.interviews) as ex:
        list(ex.map(interview, range(args.interviews)))
    wall = time.monotonic() - t0
    fu_proc.shutdown()
    aa_proc.shutdown()
    return report(results, wall, args)


def report(results, wall, args) -> int:
    print(f"\n{args.interviews} concurrent interviews x {args.turns} turns, answer ~{args.answer_seconds}s, wall {wall:.0f}s")
    ok = True
    for task, rows in results.items():
        if not rows:
            continue
        lat = [r[0] for r in rows]
        fails = sum(1 for r in rows if not r[1])
        line = (f"{task:18} jobs {len(rows):4}  rate {len(rows) / wall:5.2f}/s  p50 {statistics.median(lat):5.2f}s  "
                f"p95 {pct(lat, .95):5.2f}s  max {max(lat):5.2f}s  failed {fails}")
        if task == "FOLLOWUP":
            over = sum(1 for x in lat if x > FOLLOWUP_DEADLINE)
            good = pct(lat, .95) <= FOLLOWUP_P95_TARGET and over == 0 and fails == 0
            ok &= good
            line += f"  >12s {over}  [{'PASS' if good else 'FAIL'} p95<=6s, none over deadline]"
        print(line)
    return 0 if ok else 1


def kafka(args) -> int:
    from confluent_kafka import Consumer, Producer
    prod = Producer({"bootstrap.servers": args.bootstrap})
    cons = Consumer({"bootstrap.servers": args.bootstrap, "group.id": f"loadtest-{uuid.uuid4().hex[:6]}",
                     "auto.offset.reset": "latest"})
    topics = ["ihr.interview.followup.completed", "ihr.interview.followup.failed",
              "ihr.speech.transcription.completed", "ihr.speech.transcription.failed"]
    cons.subscribe(topics)
    sent: dict[str, tuple[str, float]] = {}
    results = {"FOLLOWUP": [], "TRANSCRIPTION": []}
    fu = json.loads((FIX / "ai/followup_decision/request.json").read_text())
    st = json.loads((FIX / "speech/transcription/request.json").read_text())
    if args.transcript_ref:
        fu["payload"]["transcript"] = json.loads(args.transcript_ref)
    if args.audio_ref:
        st["payload"]["audio"] = json.loads(args.audio_ref)
    stop = threading.Event()

    def consume():
        while not stop.is_set():
            msg = cons.poll(0.5)
            if msg is None or msg.error():
                continue
            ev = json.loads(msg.value())
            job = ev["payload"]["jobId"]
            if job in sent:
                task, t0 = sent.pop(job)
                results[task].append((time.monotonic() - t0, msg.topic().endswith(".completed")))

    th = threading.Thread(target=consume, daemon=True)
    th.start()

    def produce(task, req, topic, key):
        env = json.loads(json.dumps(req))
        env["eventId"], env["payload"]["jobId"] = str(uuid.uuid4()), str(uuid.uuid4())
        env["occurredAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        sent[env["payload"]["jobId"]] = (task, time.monotonic())
        prod.produce(topic, key=key, value=json.dumps(env).encode(), headers={"content-type": "application/json"})
        prod.poll(0)

    def interview(i):
        rng = random.Random(i)
        time.sleep(rng.uniform(0, args.answer_seconds))
        for _ in range(args.turns):
            time.sleep(rng.uniform(0.6, 1.0) * args.answer_seconds)
            produce("TRANSCRIPTION", st, "ihr.speech.transcription.requested", st["entityId"])
            time.sleep(2)
            produce("FOLLOWUP", fu, "ihr.interview.followup.requested", fu["entityId"])

    t0 = time.monotonic()
    with ThreadPoolExecutor(args.interviews) as ex:
        list(ex.map(interview, range(args.interviews)))
    prod.flush(30)
    time.sleep(30)                       # drain
    stop.set()
    th.join(5)
    cons.close()
    return report(results, time.monotonic() - t0, args)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("inprocess", "kafka"), default="inprocess")
    ap.add_argument("--interviews", type=int, default=30)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--answer-seconds", type=float, default=90.0, help="mean answer length (time between requests)")
    ap.add_argument("--followup-median", type=float, default=2.5)
    ap.add_argument("--followup-p95", type=float, default=4.5)
    ap.add_argument("--assess-median", type=float, default=8.0)
    ap.add_argument("--assess-p95", type=float, default=20.0)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--transcript-ref", help="ObjectRef JSON of a transcript in the load-test bucket (kafka mode)")
    ap.add_argument("--audio-ref", help="ObjectRef JSON of an audio clip in the load-test bucket (kafka mode)")
    args = ap.parse_args()
    return inprocess(args) if args.mode == "inprocess" else kafka(args)


if __name__ == "__main__":
    sys.exit(main())
