#!/data/data/com.termux/files/usr/bin/bash
# One-time (and every-launch) setup for Termux.
# Pulls the latest version from GitHub, installs the system packages
# discord.py/Flask need, then hands off to run.py, which checks/installs
# the Python packages and starts the UI.

set -e

echo "== Control Deck setup =="

cd "$(dirname "$0")"

echo "[1/4] Checking for updates..."
if [ -d .git ]; then
  # Termux flags repos on shared storage (e.g. /storage/emulated/0/...) as
  # untrusted since they're not owned by the Termux user; whitelist this
  # checkout so `git pull` doesn't refuse to run.
  git config --global --add safe.directory "$(pwd)" >/dev/null 2>&1 || true

  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  if git pull --ff-only origin "$BRANCH" >/dev/null 2>&1; then
    echo "  up to date with origin/$BRANCH"
  else
    echo "  couldn't auto-update (offline, or local changes) — continuing with current version"
  fi
else
  echo "  not a git checkout, skipping (see README for how to install via git)"
fi

echo "[2/4] Updating Termux package lists..."
pkg update -y >/dev/null 2>&1 || true

REQUIRED_PKGS=(python git libffi openssl)
# Optional: only needed for the !play/!join music commands. A failure here
# doesn't stop setup — bot_music.py detects what's missing and tells the
# user instead of crashing.
OPTIONAL_PKGS=(ffmpeg libsodium pkg-config clang make)

echo "[3/4] Checking Termux packages..."
for p in "${REQUIRED_PKGS[@]}"; do
  if ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    echo "  installing $p..."
    pkg install -y "$p"
  else
    echo "  $p OK"
  fi
done
for p in "${OPTIONAL_PKGS[@]}"; do
  if ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
    echo "  installing $p (for music commands)..."
    pkg install -y "$p" || echo "  couldn't install $p — music commands won't work, everything else will"
  else
    echo "  $p OK"
  fi
done

echo "[4/4] Handing off to Python..."
python run.py
