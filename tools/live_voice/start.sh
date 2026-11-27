#!/usr/bin/env bash
# Live voice interview test (dev only). Starts the STT + TTS model services and the bot.
# Needs, already running: llama-server on :8081 (local Qwen) and the test UI server on :8765.
#   tools/live_voice/start.sh webrtc     -> open http://localhost:7860
#   tools/live_voice/start.sh livekit    -> also starts livekit-server --dev; open http://localhost:7861
set -euo pipefail
MODE="${1:-webrtc}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENVS="${SPEECH_VENVS:-$HOME/idox_ai/speechtest}"
LOGS="$ROOT/tools/live_voice/logs"; mkdir -p "$LOGS"
cd "$ROOT"

for url in http://127.0.0.1:8081/health http://127.0.0.1:8765/api/health; do
  curl -sf "$url" >/dev/null || { echo "Not running: $url (start llama-server and tools/cv_parse_ui/server.py first)"; exit 1; }
done

up() { curl -sf "$1" >/dev/null 2>&1; }
up http://127.0.0.1:8091/health || { echo "Starting Nemotron STT (:8091)…"; "$VENVS/nemo-venv/bin/python" tools/live_voice/stt_server.py >"$LOGS/stt.log" 2>&1 & }
up http://127.0.0.1:8092/health || { echo "Starting Qwen3-TTS (:8092)…"; "$VENVS/qwentts/venv/bin/python" tools/live_voice/tts_server.py >"$LOGS/tts.log" 2>&1 & }
until up http://127.0.0.1:8091/health && up http://127.0.0.1:8092/health; do sleep 3; done
echo "STT + TTS ready."

if [ "$MODE" = "livekit" ]; then
  nc -z 127.0.0.1 7880 2>/dev/null || { echo "Starting livekit-server --dev (:7880)…"; livekit-server --dev --bind 127.0.0.1 >"$LOGS/livekit.log" 2>&1 & sleep 2; }
  export LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret
  exec "$VENVS/pipecat-venv/bin/python" tools/live_voice/bot.py -t livekit --port 7861
else
  exec "$VENVS/pipecat-venv/bin/python" tools/live_voice/bot.py -t webrtc --port 7860
fi
