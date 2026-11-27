# Live voice interview test (dev only)

A browser voice interview that uses the app's own pipelines. Not part of any image, and outside the V1 spec: production
speech is still the request/response Kafka jobs (faster-whisper STT, Azure/Polly TTS).

```
mic → Silero VAD → Nemotron 3.5 ASR streaming 0.6B → Smart Turn v3.2 → InterviewDirector → Qwen3-TTS 0.6B → speaker
                                                                         │
                       test UI server (:8765): FOLLOWUP · ANSWER_ASSESSMENT · FINAL_REPORT (local Qwen on :8081)
```

| Part | Where | Port |
|---|---|---|
| Nemotron STT service | `stt_server.py` (NeMo venv) | 8091 |
| Qwen3-TTS service | `tts_server.py` (qwen-tts venv) | 8092 |
| Bot, Pipecat WebRTC | `bot.py -t webrtc` (Pipecat venv) | 7860 |
| Bot, LiveKit | `bot.py -t livekit` + `livekit-server --dev` | 7861 (+7880) |

## Run
1. Start `llama-server` (:8081) and `tools/cv_parse_ui/server.py` (:8765), the same as for the test UI.
2. `tools/live_voice/start.sh webrtc` and open http://localhost:7860, **or**
   `tools/live_voice/start.sh livekit` and open http://localhost:7861.
3. Click **Connect** and allow the microphone. Use headphones, so the bot does not hear itself.
4. The bot greets you and asks the questions in `questions.json`. After each answer it says "Thank you.", then asks the
   FOLLOWUP pipeline's follow-up question (at most 1 per question, `MAX_FOLLOWUPS_PER_QUESTION`) or moves on.
5. At the end the report is written to `reports/interview-<time>.html` (and `.json`): every turn with question, transcript,
   follow-up decision, assessment scores, and the FINAL_REPORT.

Speaking while the bot talks interrupts it (barge-in). Smart Turn decides when you have finished, so short thinking
pauses do not end your answer.

## Settings (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `STT_CHUNK_MS` | 320 | Nemotron latency setting: 80 / 160 / 320 / 560 / 1120 ms |
| `STT_LANG` | en-US | Nemotron locale (en-IN is not supported; en-US or en-GB) |
| `TTS_DEVICE` | cpu | `mps` is about 3× faster, but runs out of GPU memory while local Qwen 35B is loaded |
| `TTS_REF_WAV`, `TTS_REF_TEXT` | synthetic macOS "Rishi" voice | Voice to clone. Use only recordings you have consent and rights for |
| `VAD_STOP_SECS` | 0.5 | Silero silence before Smart Turn decides |
| `QUESTIONS_FILE` | questions.json | Main questions (text, competencyCodes, expectedAnswerRubric) |
| `SPEECH_VENVS` | ~/idox_ai/speechtest | Where `start.sh` finds the three venvs |

## Known limits on a 24 GB Mac (all models local)
- Local Qwen 35B holds most of the GPU, so TTS runs on CPU. The greeting, questions, "Thank you" and closing are synthesised
  once and cached, so they play at once. Live follow-up questions take about 35–45 s to synthesise.
- STT takes about 10–20 s per 15 s answer under memory pressure (about 2.5 s with free memory).
- FOLLOWUP takes about 2.5–6 s.
- With Kimi (remote LLM) and an L4 GPU for speech, these become sub-second (STT, TTS) and 1–2 s (FOLLOWUP).
