#!/usr/bin/env python3
"""
Bootstrap launcher.

This checks that every Python package the bot/UI needs is installed
before anything else runs, installs whatever is missing with pip, and
then starts the Flask app. Run this file (not app.py directly) so the
package check always happens first:

    python run.py
"""
import importlib
import subprocess
import sys

# (import name, pip package name)
REQUIRED_PACKAGES = [
    ("flask", "flask"),
    ("discord", "discord.py"),
]


def check_and_install():
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        print("[setup] All required Python packages are already installed.")
        return

    print(f"[setup] Missing packages detected: {', '.join(missing)}")
    print("[setup] Installing now, this can take a minute on first run...")
    for pip_name in missing:
        print(f"[setup]   -> pip install {pip_name}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", pip_name],
            check=False,
        )
        if result.returncode != 0:
            print(
                f"[setup] Failed to install {pip_name}. If you're on Termux, "
                "try running setup.sh first (it installs the system packages "
                "discord.py needs to build), then run this again."
            )
            sys.exit(1)

    print("[setup] All packages installed successfully.")


def main():
    check_and_install()
    # Import after the check so we never hit an ImportError above this line.
    from app import app  # noqa: E402

    print("\n[bot-panel] Starting control panel...")
    print("[bot-panel] Open http://127.0.0.1:5000 in your browser\n")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
