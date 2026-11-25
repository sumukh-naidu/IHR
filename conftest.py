import sys
from pathlib import Path

ROOT = Path(__file__).parent
for p in ("libs/py-worker-common/src", "services/ihr-ai-worker", "services/ihr-speech-service"):
    sys.path.insert(0, str(ROOT / p))
