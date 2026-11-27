"""Live voice interview test bot (dev/test only; not part of any image).

Pipecat pipeline:
  mic -> Silero VAD -> Nemotron 3.5 ASR streaming 0.6B (stt_server.py) -> Smart Turn v3 (end of turn)
      -> InterviewDirector -> Qwen3-TTS 0.6B (tts_server.py) -> speaker

The director is not a chat LLM. After each answer it calls the app's own pipelines through the test UI server
(tools/cv_parse_ui/server.py): FOLLOWUP decides "ask a follow-up" or "next question", ANSWER_ASSESSMENT runs in the
background, and FINAL_REPORT runs at the end. The report is written to tools/live_voice/reports/.

  Pipecat WebRTC:  <pipecat-venv>/bin/python tools/live_voice/bot.py -t webrtc     -> http://localhost:7860/client
  LiveKit:         <pipecat-venv>/bin/python tools/live_voice/bot.py -t livekit    (needs livekit-server --dev)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import aiohttp
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, EndTaskFrame, ErrorFrame, Frame,
                                   InputAudioRawFrame, LLMContextFrame, TranscriptionFrame, TTSAudioRawFrame,
                                   TTSSpeakFrame, UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, LLMUserAggregatorParams
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.time import time_now_iso8601

HERE = Path(__file__).resolve().parent
STT_URL = os.environ.get("STT_URL", "http://127.0.0.1:8091")
TTS_URL = os.environ.get("TTS_URL", "http://127.0.0.1:8092")
UI_URL = os.environ.get("UI_URL", "http://127.0.0.1:8765")
QUESTIONS = json.loads((HERE / os.environ.get("QUESTIONS_FILE", "questions.json")).read_text())
MAX_FOLLOWUPS = int(os.environ.get("MAX_FOLLOWUPS_PER_QUESTION", "1"))
BARGE_IN = os.environ.get("LIVE_BARGE_IN", "0") == "1"          # 0: mic is ignored while the interviewer speaks
MIN_WORDS = int(os.environ.get("ANSWER_MIN_WORDS", "6"))         # shorter pieces ("Uh.", "Hello.") wait for more
END_PAUSE_S = float(os.environ.get("ANSWER_END_PAUSE_SECS", "2.5"))   # pause after a real answer before it is used
SHORT_ANSWER_WAIT_S = float(os.environ.get("SHORT_ANSWER_WAIT_SECS", "8"))  # "I don't know." is accepted after this
# 0 (default): ANSWER_ASSESSMENT runs after the interview, so the live part only competes with FOLLOWUP for the Mac
ASSESS_LIVE = os.environ.get("ASSESS_DURING_INTERVIEW", "0") == "1"
REPORTS = HERE / "reports"
TRANSPORT = "?"
GREETING = ("Hello, and thank you for joining. I will ask you a few questions. "
            "Take your time, and answer in your own words.")
THANKS = "Thank you."
CLOSING = "Thank you. That completes the interview. Your answers are being reviewed now. You can close this page."
SORRY = "Sorry, something went wrong on my side. Let us move on."
PREPARING = "Let me ask you something more about that."


# ---- model services (thin HTTP clients for the two local model servers) ---------------------------------
class NemotronSTTService(SegmentedSTTService):
    """Segment = the audio between Silero VAD start/stop (+ trailing silence so the last word is kept)."""

    def __init__(self, *, url: str, **kw):
        super().__init__(settings=STTSettings(model="nemotron-3.5-asr-streaming-0.6b", language=None),
                         trailing_silence_secs=1.0, **kw)
        self._url = url
        self._session: aiohttp.ClientSession | None = None

    @property
    def wants_wav_segments(self) -> bool:
        return False

    @property
    def segment_sample_rate(self) -> int:
        return 16000

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        self._session = self._session or aiohttp.ClientSession()
        await self.start_processing_metrics()
        try:
            async with self._session.post(f"{self._url}/transcribe", data=audio) as r:
                d = await r.json()
        except Exception as e:  # noqa: BLE001
            yield ErrorFrame(error=f"STT server error: {e}")
            return
        finally:
            await self.stop_processing_metrics()
        text = (d.get("text") or "").strip()
        logger.info(f"STT ({d.get('seconds')}s for {d.get('audioSeconds', 0):.1f}s audio): {text!r}")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), None)


class QwenTTSService(TTSService):
    def __init__(self, *, url: str, **kw):
        # stop_frame_timeout_s: Pipecat ends a TTS context after 3 s without audio; a live follow-up takes longer
        # to synthesise on a Mac CPU, so allow up to 60 s.
        super().__init__(push_start_frame=True, push_stop_frames=True, stop_frame_timeout_s=60.0,
                         settings=TTSSettings(model="qwen3-tts-0.6b-base", voice="ref", language=None), **kw)
        self._url = url
        self._session: aiohttp.ClientSession | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesise every sentence (cached ones are instant), then play them back to back, so a question never
        stops half-way while the next sentence is still being generated."""
        self._session = self._session or aiohttp.ClientSession()
        import re
        parts = [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if p] or [text]
        try:
            await self.start_tts_usage_metrics(text)
            audio = [await self._synth(p) for p in parts]
            await self.stop_ttfb_metrics()
            for pcm, sr in audio:
                step = sr // 5 * 2                   # 200 ms frames
                for j in range(0, len(pcm), step):
                    yield TTSAudioRawFrame(audio=pcm[j:j + step], sample_rate=sr, num_channels=1, context_id=context_id)
        except Exception as e:  # noqa: BLE001
            yield ErrorFrame(error=f"TTS server error: {e}")

    async def _synth(self, part: str) -> tuple[bytes, int]:
        async with self._session.post(f"{self._url}/synthesize", json={"text": part}) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:200]}")
            pcm, sr = await r.read(), int(r.headers.get("X-Sample-Rate", "24000"))
            logger.info(f"TTS {r.headers.get('X-Seconds')}s for {len(pcm) / 2 / sr:.1f}s audio: {part[:60]!r}")
            return pcm, sr


class MicGate(FrameProcessor):
    """Silences the microphone while the interviewer speaks (and briefly after), so its own voice leaking into the
    candidate's mic, or small noises, neither interrupt it nor become an 'answer'. Off with LIVE_BARGE_IN=1."""

    TAIL_S = 0.5

    def __init__(self, director: "InterviewDirector | None" = None):
        super().__init__()
        self.bot_speaking = False
        self.until = 0.0
        self.director = director

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.bot_speaking, self.until = False, time.monotonic() + self.TAIL_S
        elif isinstance(frame, InputAudioRawFrame) and (self.bot_speaking or time.monotonic() < self.until
                                                        or (self.director is not None and self.director.busy)):
            frame = InputAudioRawFrame(audio=b"\x00" * len(frame.audio), sample_rate=frame.sample_rate,
                                       num_channels=frame.num_channels)
        await self.push_frame(frame, direction)


# ---- interview logic ----------------------------------------------------------------------------------
class InterviewDirector(FrameProcessor):
    """Asks the planned questions; after each answer the app's FOLLOWUP pipeline decides what happens next."""

    def __init__(self, session: aiohttp.ClientSession):
        super().__init__()
        self.http = session
        self.qi = -1                 # index of the current main question
        self.followups = 0
        self.current: dict | None = None
        self.kind = "MAIN"
        self.busy = False
        self.done = False
        self.turns: list[dict] = []
        self.assessments: list[asyncio.Task] = []
        self.session = datetime.now().strftime("%H%M%S")
        self.pending_assess: list[tuple] = []
        self.pieces: list[str] = []          # speech pieces of the answer being collected
        self.asking = False                  # a question was pushed; waiting for it to be spoken
        self.asked_started = False
        self.timer: asyncio.Task | None = None

    async def _post(self, path: str, body: dict) -> dict:
        async with self.http.post(f"{UI_URL}{path}", json=body, timeout=aiohttp.ClientTimeout(total=900)) as r:
            return await r.json()

    async def say(self, text: str) -> None:
        await self.push_frame(TTSSpeakFrame(text))

    async def event(self, type_: str, **data) -> None:
        """Live feed for the main test UI (tab 8). Never blocks or breaks the interview."""
        try:
            async with self.http.post(f"{UI_URL}/api/live/event", json={"session": self.session, "type": type_, **data},
                                      timeout=aiohttp.ClientTimeout(total=3)) as r:
                await r.read()
        except Exception:  # noqa: BLE001
            pass

    async def prewarm(self) -> None:
        """Synthesise the fixed lines now (TTS server caches them), so only follow-ups are generated live."""
        import re
        lines = [GREETING, THANKS, CLOSING, SORRY, PREPARING] + [q["text"] for q in QUESTIONS]
        for t in [p for line in lines for p in re.split(r"(?<=[.?!])\s+", line) if p]:
            try:
                async with self.http.post(f"{TTS_URL}/synthesize", json={"text": t}) as r:
                    await r.read()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"TTS prewarm failed: {e}")
        logger.info("TTS prewarm done (greeting, questions, closing cached)")

    async def start_interview(self) -> None:
        self.busy = True
        await self.event("started", transport=TRANSPORT, questions=len(QUESTIONS))
        await self.say(GREETING)
        await self.next_question()

    async def next_question(self) -> None:
        if self.done:
            return
        self.qi += 1
        self.followups = 0
        if self.qi >= len(QUESTIONS):
            return await self.finish()
        self.current, self.kind = QUESTIONS[self.qi], "MAIN"
        logger.info(f"Q{self.qi + 1}: {self.current['text']}")
        await self.event("question", kind="MAIN", n=self.qi + 1, text=self.current["text"])
        await self.ask(self.current["text"])

    async def ask(self, text: str) -> None:
        """Speak a question; the mic stays closed (busy) until it has been spoken in full."""
        self.asking, self.asked_started = True, False
        await self.say(text)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame) and self.asking:
            self.asked_started = True
        elif isinstance(frame, BotStoppedSpeakingFrame) and self.asking and self.asked_started:
            self.asking = False
            self.busy = False                        # question spoken: the candidate may answer now
        if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)) and self.timer:
            self.timer.cancel()                      # still talking: keep collecting
            self.timer = None
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            msgs = [m for m in frame.context.get_messages() if m.get("role") == "user"]
            text = (msgs[-1].get("content") if msgs else "") or ""
            text = text if isinstance(text, str) else " ".join(p.get("text", "") for p in text if isinstance(p, dict))
            if text.strip() and not self.busy and not self.done and self.current:
                self.pieces.append(text.strip())
                words = len(" ".join(self.pieces).split())
                logger.info(f"heard piece ({words} words so far): {text.strip()!r}")
                await self.event("partial", text=" ".join(self.pieces), words=words)
                if self.timer:
                    self.timer.cancel()
                self.timer = asyncio.create_task(self._answer_after(END_PAUSE_S if words >= MIN_WORDS else SHORT_ANSWER_WAIT_S))
            return                                   # the context frame stops here (no chat LLM in this pipeline)
        await self.push_frame(frame, direction)

    async def _answer_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self.timer = None
        answer, self.pieces = " ".join(self.pieces).strip(), []
        if answer and not self.busy and not self.done:
            await self.on_answer(answer)

    def stop(self) -> None:
        """The candidate left: stop everything this session started."""
        self.done = True
        if self.timer:
            self.timer.cancel()

    async def on_answer(self, answer: str) -> None:
        if self.done:
            return
        self.busy = True
        try:
            q = {**self.current, "text": self.current["text"]}
            logger.info(f"Answer ({self.kind}): {answer}")
            t = await self._post("/api/transcript/text", {"text": answer, "model": "nemotron-3.5-asr-streaming-0.6b"})
            tid = t["transcriptId"]
            await self.say(THANKS)                    # immediate acknowledgement while FOLLOWUP runs
            turn = {"n": len(self.turns) + 1, "kind": self.kind, "question": q["text"], "answer": answer}
            self.turns.append(turn)
            await self.event("answer", turn=turn["n"], kind=self.kind, text=answer)
            # ANSWER_ASSESSMENT: after the interview by default (see ASSESS_LIVE); the candidate never waits for it
            if ASSESS_LIVE:
                self.assessments.append(asyncio.create_task(self._assess(tid, q, self.kind, turn)))
            else:
                self.pending_assess.append((tid, q, self.kind, turn))
            decision = None
            if self.kind == "MAIN" and self.followups < MAX_FOLLOWUPS:
                t0 = time.monotonic()
                fu = (await self._post("/api/evaluate", {"transcriptId": tid, "question": q, "runFollowup": True,
                                                         "runAssessment": False, "followupDeadline": 60}))["followup"]
                decision = (fu.get("payload") or {}).get("output") or {}
                turn["followup"] = {"decision": decision, "seconds": round(time.monotonic() - t0, 1)}
                logger.info(f"FOLLOWUP {turn['followup']['seconds']}s: {decision}")
                await self.event("decision", turn=turn["n"], decision=decision.get("decision"),
                                 reason=decision.get("reasonCode"), seconds=turn["followup"]["seconds"])
            if self.done:
                return
            if decision and decision.get("decision") == "ASK_FOLLOW_UP" and (decision.get("followUp") or {}).get("text"):
                self.followups += 1
                fq = decision["followUp"]
                self.current = {**self.current, "text": fq["text"],
                                "competencyCodes": fq.get("competencyCodes") or self.current["competencyCodes"]}
                self.kind = "FOLLOW_UP"
                await self.event("question", kind="FOLLOW_UP", n=self.qi + 1, text=fq["text"])
                await self.ask(fq["text"])
            else:
                await self.next_question()
        except Exception as e:  # noqa: BLE001
            logger.exception(e)
            await self.say(SORRY)
            await self.next_question()
        finally:
            if not self.asking:                      # no question pending (finished or failed): reopen the mic
                self.busy = False

    async def _assess(self, tid: str, q: dict, kind: str, turn: dict) -> None:
        d = await self._post("/api/evaluate", {"transcriptId": tid, "question": q, "turnKind": kind,
                                               "runFollowup": False, "runAssessment": True})
        a = d.get("assessment") or {}
        turn["evalId"] = a.get("evalId")
        turn["assessment"] = a.get("assessment")
        turn["assessmentError"] = None if a.get("evalId") else ((a.get("payload") or {}).get("errorCode") or d.get("error"))
        sc = [{"code": s["competencyCode"], "score": s["score"]} for s in (a.get("assessment") or {}).get("scores", [])]
        logger.info(f"ASSESSMENT T{turn['n']}: {sc}")
        await self.event("assessment", turn=turn["n"], scores=sc, error=turn["assessmentError"])

    async def finish(self) -> None:
        self.done = True
        await self.say(CLOSING)
        own = aiohttp.ClientSession()              # survives the call ending (the pipeline's session is closed)
        self.http = own
        await self.event("finishing")
        for args in self.pending_assess:            # one at a time: the LLM serves one request at a time anyway
            try:
                await self._assess(*args)
            except Exception as e:  # noqa: BLE001
                args[3]["assessmentError"] = str(e)
        await asyncio.gather(*self.assessments, return_exceptions=True)
        ids = [t["evalId"] for t in self.turns if t.get("evalId")]
        async with own:
            report = (await self._post("/api/report", {"evalIds": ids})) if ids else {"error": "no assessed answers"}
            REPORTS.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = {"turns": self.turns, "report": report.get("report"), "computedScores": report.get("computedScores"),
                   "reportError": report.get("error") or (report.get("payload") or {}).get("errorCode")}
            (REPORTS / f"interview-{stamp}.json").write_text(json.dumps(out, indent=1))
            _write_html(REPORTS / f"interview-{stamp}.html", out)
            logger.success(f"REPORT written: {REPORTS / f'interview-{stamp}.html'}")
            await self.event("report", name=f"interview-{stamp}", error=out["reportError"])


def _write_html(path: Path, d: dict) -> None:
    import html
    def scores(t: dict) -> str:
        sc = (t.get("assessment") or {}).get("scores") or []
        return ", ".join(f"{s['competencyCode']}: {s['score']}" for s in sc) or str(t.get("assessmentError") or "")

    def decision(t: dict) -> str:
        return str(((t.get("followup") or {}).get("decision") or {}).get("decision") or "")

    rows = "".join(
        f"<tr><td>T{t['n']} {t['kind']}</td><td>{html.escape(t['question'])}</td><td>{html.escape(t['answer'])}</td>"
        f"<td>{html.escape(decision(t))}</td><td>{html.escape(scores(t))}</td></tr>"
        for t in d["turns"])
    rep = d.get("report") or {}
    body = (f"<h1>Live interview report</h1><h2>Summary</h2><p>{html.escape(str(rep.get('summary') or d.get('reportError')))}</p>"
            f"<h2>Computed scores (core stand-in)</h2><pre>{html.escape(json.dumps(d.get('computedScores'), indent=1))}</pre>"
            f"<h2>Turns</h2><table border=1 cellpadding=6><tr><th>Turn</th><th>Question</th><th>Answer (STT)</th>"
            f"<th>Follow-up decision</th><th>Scores</th></tr>{rows}</table>"
            f"<h2>Full report JSON</h2><pre>{html.escape(json.dumps(rep, indent=1))}</pre>")
    path.write_text(f"<!doctype html><meta charset=utf-8><title>Interview report</title><body style='font-family:sans-serif;max-width:1100px;margin:auto'>{body}</body>")


# ---- bot entry point (Pipecat runner) ---------------------------------------------------------------------
async def bot(runner_args: RunnerArguments):
    params = lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)  # noqa: E731
    lk = lambda: __import__("pipecat.transports.livekit.transport", fromlist=["LiveKitParams"]).LiveKitParams(  # noqa: E731
        audio_in_enabled=True, audio_out_enabled=True)
    transport = await create_transport(runner_args, {"webrtc": params, "livekit": lk})
    global TRANSPORT
    TRANSPORT = type(transport).__name__
    session = aiohttp.ClientSession()
    director = InterviewDirector(session)
    context = LLMContext()
    user_agg = LLMUserAggregator(context, params=LLMUserAggregatorParams())   # default stop = Smart Turn v3
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(
        stop_secs=float(os.environ.get("VAD_STOP_SECS", "0.5")),
        start_secs=float(os.environ.get("VAD_START_SECS", "0.3")),       # ignore clicks and short noises
        confidence=float(os.environ.get("VAD_CONFIDENCE", "0.75")),
        min_volume=float(os.environ.get("VAD_MIN_VOLUME", "0.65")))))
    stages = [transport.input()] + ([] if BARGE_IN else [MicGate(director)]) + [vad]
    pipeline = Pipeline([*stages, NemotronSTTService(url=STT_URL), user_agg, director,
                         QwenTTSService(url=TTS_URL), transport.output()])
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    prewarm = asyncio.create_task(director.prewarm())

    started = asyncio.Event()

    async def _start(*_):
        if started.is_set():                     # LiveKit can fire more than one "joined" event
            return
        started.set()
        await asyncio.sleep(1.0)
        await prewarm                            # first connection waits for the cached lines (once per bot session)
        await director.start_interview()

    for ev in ("on_client_connected", "on_first_participant_joined"):
        try:
            transport.event_handler(ev)(_start)
        except Exception:  # noqa: BLE001 - each transport has only one of the two
            pass

    async def _left(*_):
        director.stop()
        await task.cancel()

    for ev in ("on_client_disconnected", "on_participant_left"):
        try:
            transport.event_handler(ev)(_left)
        except Exception:  # noqa: BLE001
            pass

    @task.event_handler("on_pipeline_finished")
    async def _closed(*_):
        await session.close()

    await PipelineRunner(handle_sigint=False).run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
