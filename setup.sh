#!/data/data/com.termux/files/usr/bin/bash
# One-time (and every-launch) setup for Termux.
# Pulls the latest version from GitHub, installs the system packages
# discord.py/Flask need, then hands off to run.py, which checks/installs
# the Python packages and starts the UI. Prints a short progress line for
# each step, and skips `pkg update` entirely when nothing actually needs
# installing — that network round-trip was the main thing making repeat
# launches (the common case, once packages are already present) slow.

set -e

cd "$(dirname "$0")"

echo "==> Checking for updates..."
if [ -d .git ]; then
  # Termux flags repos on shared storage (e.g. /storage/emulated/0/...) as
  # untrusted since they're not owned by the Termux user; whitelist this
  # checkout so `git pull` doesn't refuse to run.
  git config --global --add safe.directory "$(pwd)" >/dev/null 2>&1 || true

  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  git pull --ff-only origin "$BRANCH" >/dev/null 2>&1 || true
fi

REQUIRED_PKGS=(python git libffi openssl)
# Optional: only needed for !play/!join (music) and !tts (voice). A failure
# here doesn't stop setup — the relevant command detects what's missing and
# tells the user instead of crashing.
OPTIONAL_PKGS=(ffmpeg libsodium pkg-config clang make espeak-ng)

echo "==> Checking system packages..."
# One listing instead of one `pkg list-installed` per package (that command
# re-reads Termux's whole package database each time — noticeably slower
# than reading it once and grepping the saved text repeatedly).
INSTALLED="$(pkg list-installed 2>/dev/null)"

MISSING_REQUIRED=()
for p in "${REQUIRED_PKGS[@]}"; do
  echo "$INSTALLED" | grep -q "^$p/" || MISSING_REQUIRED+=("$p")
done

MISSING_OPTIONAL=()
for p in "${OPTIONAL_PKGS[@]}"; do
  echo "$INSTALLED" | grep -q "^$p/" || MISSING_OPTIONAL+=("$p")
done

if [ ${#MISSING_REQUIRED[@]} -gt 0 ] || [ ${#MISSING_OPTIONAL[@]} -gt 0 ]; then
  echo "==> Installing: ${MISSING_REQUIRED[*]} ${MISSING_OPTIONAL[*]}"
  pkg update -y >/dev/null 2>&1 || true

  for p in "${MISSING_REQUIRED[@]}"; do
    if ! pkg install -y "$p" >/dev/null 2>&1; then
      echo "Error: couldn't install required package '$p'. Try running: pkg install $p" >&2
      exit 1
    fi
  done

  for p in "${MISSING_OPTIONAL[@]}"; do
    pkg install -y "$p" >/dev/null 2>&1 || true
  done
else
  echo "==> All system packages already installed."
fi

echo "==> Starting Control Deck..."
python run.py
