import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
ROOT = EVAL.parent
for p in (EVAL, ROOT / "services/ihr-ai-worker/tests"):
    sys.path.insert(0, str(p))
