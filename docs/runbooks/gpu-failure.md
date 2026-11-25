# Runbook: GPU / STT failure

**Alerts:** `GpuUnavailable` (ready STT pods = 0 for 2 min), `SttLatencyHigh` (p95 > 6 s for 10 min).

## What the system already does
- Pods report ready only after the model loaded and the warm-up clip transcribed.
- CUDA out-of-memory → model reloaded once and the job retried; a second OOM → `INTERNAL_ERROR` (retryable) and
  liveness fails, so Kubernetes restarts the pod.
- Core: transcription not done in 25 s → next planned question; transcription finishes later (S-09).

## Check
1. `kubectl get pods -l app=ihr-speech-stt`, `kubectl describe pod` (GPU node, image pull, OOMKilled).
2. `kubectl logs` for `whisper_ready` / `whisper_oom_reloading` events.
3. Node: `nvidia-smi` on the GPU node; driver / device-plugin status.
4. Latency only: `ihr_stt_duration_seconds` vs `ihr_stt_audio_seconds` (real-time factor), queue depth on
   `ihr.speech.transcription.requested`.

## Act
5. Pod crash-looping on load: verify the image's model checksum step passed and `WHISPER_MODEL_DIR` matches.
6. GPU node lost: cordon/replace the node (Infra). Until back, transcriptions queue (Kafka retains them) and
   assessments run once STT returns.
7. Sustained latency: enable faster-whisper BatchedInferencePipeline (batch size 8) per §11.1 before
   adding a second GPU; re-run `tools/stt_benchmark.py --gate`.
8. Emergency only (AI lead approval): a CPU deployment with `WHISPER_MODEL=small.en`, `int8` keeps transcripts
   flowing at lower accuracy; mark the period in the incident note (assessments get LOW confidence if quality
   flags fire).
