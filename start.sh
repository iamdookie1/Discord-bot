#!/data/data/com.termux/files/usr/bin/bash
# Control Deck — one-command Termux launcher.
#
# This is the "I forgot how to run it" file. It handles everything:
#   1. Clones the repo if you don't have it yet, otherwise pulls the latest.
#   2. Makes sure Termux's system packages are installed (skips the slow
#      `pkg update` when nothing is actually missing).
#   3. Starts the Control Deck.
#
# Two ways to use it:
#
#   A) FIRST TIME, from nothing — paste this one line into Termux:
#
#        pkg install -y curl && curl -fsSL \
#          https://raw.githubusercontent.com/iamdookie1/discord-bot/main/start.sh \
#          -o start.sh && bash start.sh
#
#   B) EVERY TIME AFTER, once you have the repo — from inside it just run:
#
#        bash start.sh
#
# Repeat launches take a fast path: if the dependencies haven't changed
# since the last successful setup, the whole package-checking phase is
# skipped and it jumps straight to starting the app.

set -e

REPO_URL="https://github.com/iamdookie1/discord-bot.git"
# Where to keep the checkout. Override with:  DIR=~/mybot bash start.sh
DIR="${DIR:-$HOME/Discord-bot}"

# --- 1. Get or update the code ------------------------------------------
# If this script is already running from inside a checkout, stay there.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$SCRIPT_DIR/.git" ] && [ -f "$SCRIPT_DIR/run.py" ]; then
  DIR="$SCRIPT_DIR"
fi

if [ -d "$DIR/.git" ]; then
  echo "==> Updating existing copy in $DIR ..."
  # Termux flags repos on shared storage as untrusted (not owned by the
  # Termux user); whitelist this one so git won't refuse to run.
  git config --global --add safe.directory "$DIR" >/dev/null 2>&1 || true
  cd "$DIR"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  git pull --ff-only origin "$BRANCH" >/dev/null 2>&1 || true
else
  echo "==> Cloning Control Deck into $DIR ..."
  command -v git >/dev/null 2>&1 || pkg install -y git >/dev/null 2>&1 || true
  # --depth 1 grabs only the latest commit — a much faster first download
  # than the full history, which you don't need just to run the bot.
  git clone --depth 1 "$REPO_URL" "$DIR"
  cd "$DIR"
fi

# --- 2. System packages (with a fast path) ------------------------------
REQUIRED_PKGS=(python git libffi openssl)
# Optional: only needed for !play/!join (music) and !tts (voice). A missing
# one doesn't stop setup — the command tells you what to install instead.
OPTIONAL_PKGS=(ffmpeg libsodium pkg-config clang make espeak-ng)

# A stamp of what we last set up. If it still matches, every system package
# we care about was present last time and nothing in the list has changed,
# so we skip the `pkg list-installed` scan entirely and launch faster.
STAMP_FILE="$DIR/.cd_setup_stamp"
WANT_STAMP="$(printf '%s\n' "${REQUIRED_PKGS[@]}" "${OPTIONAL_PKGS[@]}")"

if [ -f "$STAMP_FILE" ] && [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$WANT_STAMP" ]; then
  echo "==> System packages already set up (fast path)."
else
  echo "==> Checking system packages..."
  # One listing + repeated greps is much faster than one `pkg list-installed`
  # per package (each call re-reads Termux's whole package database).
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
        echo "Error: couldn't install required package '$p'. Try: pkg install $p" >&2
        exit 1
      fi
    done

    for p in "${MISSING_OPTIONAL[@]}"; do
      pkg install -y "$p" >/dev/null 2>&1 || true
    done
  fi

  # Only required packages gate the stamp — optional ones are allowed to be
  # missing, so we don't want their absence to force a full re-scan forever.
  MISSING_REQUIRED_NOW=()
  INSTALLED="$(pkg list-installed 2>/dev/null)"
  for p in "${REQUIRED_PKGS[@]}"; do
    echo "$INSTALLED" | grep -q "^$p/" || MISSING_REQUIRED_NOW+=("$p")
  done
  if [ ${#MISSING_REQUIRED_NOW[@]} -eq 0 ]; then
    printf '%s' "$WANT_STAMP" > "$STAMP_FILE" 2>/dev/null || true
  fi
fi

# --- 3. Launch ----------------------------------------------------------
echo "==> Starting Control Deck..."
python run.py
