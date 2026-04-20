#!/bin/bash
# deploy.sh — called after the bot writes a branch name to 'target_branch' and shuts down.
# systemd restarts the bot process, which triggers this script via ExecStartPre, OR
# you can run it as a wrapper (see README for systemd setup).

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_BRANCH_FILE="$REPO_DIR/target_branch"

cd "$REPO_DIR"

# Read the target branch, defaulting to 'main' if the file is missing or empty.
if [ -f "$TARGET_BRANCH_FILE" ] && [ -s "$TARGET_BRANCH_FILE" ]; then
    BRANCH=$(cat "$TARGET_BRANCH_FILE")
else
    BRANCH="main"
fi

echo "[deploy] Switching to branch: $BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "[deploy] Installing/updating dependencies..."
source "$REPO_DIR/venv/bin/activate"
pip install -q -r requirements.txt

echo "[deploy] Done. Starting bot on branch: $BRANCH"
exec python gnk_bot.py
