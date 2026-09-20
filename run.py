"""Start the web app.

    python run.py                      a model writes the wording if a key is set —
                                       ANTHROPIC_API_KEY for Claude, GEMINI_API_KEY for Gemini —
                                       otherwise the template does
    python run.py --drafter template   force the template, even with a key set
"""

import argparse

from app.server import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--drafter", choices=["auto", "template", "claude", "gemini"], default="auto")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    create_app(drafter=args.drafter).run(host="127.0.0.1", port=args.port, debug=False)
