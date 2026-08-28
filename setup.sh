#!/data/data/com.termux/files/usr/bin/bash
# One-time (and every-launch) setup for Termux.
# Pulls the latest version from GitHub, installs the system packages
# discord.py/Flask need, then hands off to run.py, which checks/installs
# the Python packages and starts the UI. Silent on success — only prints
# something if a required step actually fails.

set -e

cd "$(dirname "$0")"

if [ -d .git ]; then
  # Termux flags repos on shared storage (e.g. /storage/emulated/0/...) as
  # untrusted since they're not owned by the Termux user; whitelist this
  # checkout so `git pull` doesn't refuse to run.
  git config --global --add safe.directory "$(pwd)" >/dev/null 2>&1 || true

  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  git pull --ff-only origin "$BRANCH" >/dev/null 2>&1 || true
fi

pkg update -y >/dev/null 2>&1 || true

REQUIRED_PKGS=(python git libffi openssl)
# Optional: only needed for !play/!join (music), !tts (voice), and the
# standalone Voice Speaker app's playback (termux-api, for
# termux-media-player). A failure here doesn't stop setup — the relevant
# command/app detects what's missing and tells the user instead of
# crashing. termux-api is the CLI half only — actually playing audio also
# needs the separate Termux:API Android app (F-Droid/Play Store), which
# this script can't install for you.
OPTIONAL_PKGS=(ffmpeg libsodium pkg-config clang make espeak-ng termux-api)

for p in "${REQUIRED_PKGS[@]}"; do
  if ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    if ! pkg install -y "$p" >/dev/null 2>&1; then
      echo "Error: couldn't install required package '$p'. Try running: pkg install $p" >&2
      exit 1
    fi
  fi
done

for p in "${OPTIONAL_PKGS[@]}"; do
  if ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    pkg install -y "$p" >/dev/null 2>&1 || true
  fi
done

# Two separate apps live in this repo: the Discord bot (root), and the
# standalone Voice Speaker (voicespeak/) — type text, this device reads it
# aloud, no Discord connection involved. Pick one non-interactively with
# `bash setup.sh bot` / `bash setup.sh voice`, or get prompted below.
CHOICE="$1"
if [ -z "$CHOICE" ]; then
  echo "1) Discord bot"
  echo "2) Voice speaker"
  read -p "Choose [1/2]: " CHOICE
fi

case "$CHOICE" in
  2|voice|voicespeak)
    cd voicespeak && python run.py
    ;;
  *)
    python run.py
    ;;
esac
