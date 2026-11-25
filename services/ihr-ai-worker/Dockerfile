# ihr-ai-worker (AI Specification v1.1 §3.4). Build from the repo root:
#   docker build -f services/ihr-ai-worker/Dockerfile -t ihr-ai-worker .
FROM python:3.12-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libreoffice-writer-nogui fonts-dejavu \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY libs/py-worker-common /app/libs/py-worker-common
COPY services/ihr-ai-worker /app/services/ihr-ai-worker
COPY contracts /app/contracts
RUN uv pip install --system /app/libs/py-worker-common /app/services/ihr-ai-worker

ENV IHR_CONTRACTS_DIR=/app/contracts \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home ihr
USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "ihr_ai_worker.main"]
