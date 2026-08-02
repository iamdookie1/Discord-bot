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
# Core: the app can't run without these, so a failed install stops startup.
REQUIRED_PACKAGES = [
    ("flask", "flask"),
    ("discord", "discord.py"),
]

# Extra: pre-imported by bot_commands.py so custom commands can use them
# right away. Each one is wrapped in a try/except at import time there, so
# if one fails to install here the app still starts — that command feature
# just won't have that particular module available.
EXTRA_PACKAGES = [
    ("requests", "requests"),
    ("pytz", "pytz"),
    ("dateutil", "python-dateutil"),
    ("humanize", "humanize"),
    ("emoji", "emoji"),
    ("bs4", "beautifulsoup4"),
    ("yaml", "PyYAML"),
    ("colorama", "colorama"),
    ("tabulate", "tabulate"),
    ("validators", "validators"),
]


def _missing(packages):
    missing = []
    for import_name, pip_name in packages:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def _pip_install(pip_name):
    print(f"[setup]   -> pip install {pip_name}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", pip_name],
        check=False,
    )
    return result.returncode == 0


def check_and_install():
    missing_core = _missing(REQUIRED_PACKAGES)
    missing_extra = _missing(EXTRA_PACKAGES)

    if not missing_core and not missing_extra:
        print("[setup] All required Python packages are already installed.")
        return

    if missing_core:
        print(f"[setup] Missing core packages: {', '.join(missing_core)}")
    if missing_extra:
        print(f"[setup] Missing extra packages (used by custom commands): {', '.join(missing_extra)}")
    print("[setup] Installing now, this can take a minute on first run...")

    for pip_name in missing_core:
        if not _pip_install(pip_name):
            print(
                f"[setup] Failed to install {pip_name}. If you're on Termux, "
                "try running setup.sh first (it installs the system packages "
                "discord.py needs to build), then run this again."
            )
            sys.exit(1)

    for pip_name in missing_extra:
        if not _pip_install(pip_name):
            print(f"[setup] Couldn't install {pip_name} — continuing without it.")

    print("[setup] Package check complete.")


def main():
    check_and_install()
    # Import after the check so we never hit an ImportError above this line.
    from app import app  # noqa: E402

    print("\n[bot-panel] Starting control panel...")
    print("[bot-panel] Open http://127.0.0.1:5000 in your browser\n")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
