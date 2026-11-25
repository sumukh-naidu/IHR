"""FastAPI app exposing only /health/*, /metrics and /version (A-01)."""
from __future__ import annotations

import threading
from typing import Callable

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .events import worker_version


def build_app(service: str, ready: Callable[[], bool] = lambda: True,
              alive: Callable[[], bool] = lambda: True) -> FastAPI:
    app = FastAPI(title=service, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    def live() -> Response:
        return Response(status_code=200 if alive() else 503)

    @app.get("/health/ready")
    def readiness() -> Response:
        return Response(status_code=200 if ready() else 503)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/version")
    def version() -> dict:
        return {"service": service, "version": worker_version()}

    return app


def serve_in_background(app: FastAPI, port: int) -> threading.Thread:
    import uvicorn

    t = threading.Thread(target=uvicorn.run, args=(app,),
                         kwargs={"host": "0.0.0.0", "port": port, "log_level": "warning"}, daemon=True)
    t.start()
    return t
