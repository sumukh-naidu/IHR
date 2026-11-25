"""S-11 Sprint 1 check: list GET /v1/models and confirm both configured model slots exist.

Usage: KIMI_API_KEY=... python tools/kimi_list_models.py
Exit 0 if KIMI_MODEL_STANDARD and KIMI_MODEL_REALTIME are both listed. Prints model ids only.
"""
import os
import sys

import openai


def main() -> int:
    client = openai.OpenAI(base_url=os.environ.get("KIMI_API_BASE_URL", "https://api.moonshot.ai/v1"),
                           api_key=os.environ["KIMI_API_KEY"], max_retries=0, timeout=20)
    ids = sorted(m.id for m in client.models.list())
    print("\n".join(ids))
    slots = {"KIMI_MODEL_STANDARD": os.environ.get("KIMI_MODEL_STANDARD", "kimi-k2-0905-preview"),
             "KIMI_MODEL_REALTIME": os.environ.get("KIMI_MODEL_REALTIME", "kimi-k2-turbo-preview")}
    ok = True
    for name, model in slots.items():
        found = model in ids
        ok &= found
        print(f"{name}={model}: {'found' if found else 'NOT FOUND'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
