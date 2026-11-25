# ihr-speech-service, mock/TTS image (AI Specification v1.1 §3.4).
# The GPU STT image is Dockerfile.stt (CUDA + faster-whisper + baked large-v3-turbo).
#   docker build -f services/ihr-speech-service/Dockerfile -t ihr-speech-service .
FROM python:3.12-slim-trixie

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY libs/py-worker-common /app/libs/py-worker-common
COPY services/ihr-speech-service /app/services/ihr-speech-service
COPY contracts /app/contracts
RUN uv pip install --system /app/libs/py-worker-common /app/services/ihr-speech-service

ENV IHR_CONTRACTS_DIR=/app/contracts \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home ihr
USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "speech.main"]
