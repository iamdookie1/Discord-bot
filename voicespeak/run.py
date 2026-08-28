#!/usr/bin/env python3
"""
Bootstrap launcher for the standalone Voice Speaker app — checks Flask is
installed, installs it if not, then starts the server. Run this file (not
app.py directly):

    python run.py
"""
import importlib
import subprocess
import sys


def _ensure_flask():
    try:
        importlib.import_module("flask")
        return
    except ImportError:
        pass
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "flask"], check=False)
    if result.returncode != 0:
        print("Error: couldn't install Flask.", file=sys.stderr)
        sys.exit(1)


def main():
    _ensure_flask()
    from app import app  # noqa: E402

    print("Open http://127.0.0.1:5050 in your browser")
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
