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
import os
import subprocess
import sys
import time

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
    ("nacl", "PyNaCl"),
    ("davey", "davey"),
    ("yt_dlp", "yt-dlp"),
    ("qrcode", "qrcode"),
    ("pyfiglet", "pyfiglet"),
]

PIP_TIMEOUT_SECONDS = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# yt-dlp gets re-checked for updates below regardless of whether it's
# already installed (see check_and_install) — that's a real network round
# trip every time, so it's throttled to once per this many seconds instead
# of literally every launch, which is the more common case once you're
# actively testing/restarting the bot.
YT_DLP_CHECK_MARKER = os.path.join(BASE_DIR, ".yt_dlp_last_check")
YT_DLP_CHECK_INTERVAL = 6 * 60 * 60


def _missing(packages):
    missing = []
    for import_name, pip_name in packages:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


# By default PyNaCl tries to download and compile its own bundled copy of
# libsodium from source, which is what "Failed building wheel for PyNaCl"
# usually is on Termux (no autoconf/libtool for that build). Setting
# SODIUM_INSTALL=system instead tells it to link against the libsodium
# setup.sh already installs via `pkg install libsodium` — much more likely
# to actually succeed.
_PIP_ENV_OVERRIDES = {
    "PyNaCl": {"SODIUM_INSTALL": "system"},
}


def _pip_install(pip_name):
    env = os.environ.copy()
    env.update(_PIP_ENV_OVERRIDES.get(pip_name, {}))
    args = [
        sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
        "--disable-pip-version-check", pip_name,
    ]
    try:
        result = subprocess.run(args, check=False, env=env, timeout=PIP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print(f"  {pip_name}: timed out after {PIP_TIMEOUT_SECONDS}s — check your connection, continuing anyway.")
        return False
    return result.returncode == 0


def _yt_dlp_check_is_stale() -> bool:
    try:
        return (time.time() - os.path.getmtime(YT_DLP_CHECK_MARKER)) > YT_DLP_CHECK_INTERVAL
    except OSError:
        return True  # never checked before


def check_and_install():
    print("Checking Python packages...")
    missing_core = _missing(REQUIRED_PACKAGES)
    missing_extra = _missing(EXTRA_PACKAGES)

    if missing_core or missing_extra:
        print(f"  installing: {', '.join(missing_core + missing_extra)}")

    for pip_name in missing_core:
        if not _pip_install(pip_name):
            print(
                f"Error: couldn't install required package '{pip_name}'. If you're on "
                "Termux, run setup.sh first (it installs the system packages discord.py "
                "needs to build), then run this again.",
                file=sys.stderr,
            )
            sys.exit(1)

    for pip_name in missing_extra:
        # Non-fatal — the feature that needs it (e.g. !play, !tts) tells the
        # user what's missing when it's actually used, instead of here.
        _pip_install(pip_name)

    # yt-dlp breaks against YouTube regularly as YouTube changes its
    # anti-bot measures, and yt-dlp's own advice for "playback failed" /
    # 403 errors is almost always "update yt-dlp first" — so unlike the
    # other extras, it's worth re-checking for a newer version regularly
    # instead of only installing it once and leaving it stale. Throttled
    # to once per YT_DLP_CHECK_INTERVAL (see above) rather than literally
    # every launch, since that's a real network round trip every time.
    if _yt_dlp_check_is_stale():
        print("Checking for yt-dlp updates...")
        _pip_install("yt-dlp")
        try:
            with open(YT_DLP_CHECK_MARKER, "w"):
                pass
        except OSError:
            pass


def main():
    check_and_install()
    # Import after the check so we never hit an ImportError above this line.
    from app import app  # noqa: E402

    print("Open http://127.0.0.1:5000 in your browser")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
