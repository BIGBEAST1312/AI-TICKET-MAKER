"""Send one real drafting request and print exactly what comes back.

    python scripts/check_model.py

Uses the same code path as the app, so if this works the app works. On failure
it prints the provider's own error message rather than just its type.
"""

import json
import os
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drafting  # noqa: E402

drafter = drafting.Drafter()
print(f"drafter: {drafter.drafter}")
if drafter.drafter not in drafting.PROVIDERS:
    sys.exit("No ANTHROPIC_API_KEY or GEMINI_API_KEY is set in this terminal.")

provider = drafting.PROVIDERS[drafter.drafter]
print(f"model:   {os.environ.get('TICKET_MAKER_MODEL', provider['model'])}")

call = drafter._call_gemini if drafter.drafter == "gemini" else drafter._call_claude
user = "Mode: staff\nClient interaction: Client cannot connect to uofs-secure on a Chromebook\nStaff notes: removed the saved network, confirmed connected"
try:
    print(json.dumps(call(drafting.SYSTEM_PROMPT, user), indent=2))
    print(f"\nOK: answered by {getattr(drafter, 'last_model', None) or 'the model'}. The app will draft with it.")
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}")
    print(exc.read().decode("utf-8", "replace"))
except Exception as exc:
    print(f"{exc.__class__.__name__}: {exc}")
