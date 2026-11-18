#!/usr/bin/env bash
# The Slack demo (plan P-57, docs/slack-demo-runbook.md), with uv on macOS or Linux.
#
#   scripts/slack-demo.sh start   stop anything it started, reset the demo folder, start
#                                 the runner, the UI (:8501) and the Slack app
#   scripts/slack-demo.sh stop    stop them
#
# The demo lives in its own folder, data/demo by default (DEMO_DIR=... to change), so
# starting over never touches ./data. Settings and secrets come from .env (SLACK_*,
# NOTIFY_CHANNELS, SMTP_*); the AI runs in replay mode: no Claude API calls.
# Start it no more than 3 minutes before presenting: the runner imports the queued AE
# file by itself 5 minutes after it starts, and the demo opens with that import in Slack.
set -euo pipefail

cd "$(dirname "$0")/.."
DEMO_DIR="${DEMO_DIR:-data/demo}"
PIDS="$DEMO_DIR/demo.pids"
export ADJUD_DATA_DIR="$DEMO_DIR" ADJUD_AI_MODE=replay

stop() {
  if [[ -f "$PIDS" ]]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
    rm -f "$PIDS"
    sleep 2
  fi
}

start() {
  [[ -f .env ]] || { echo "No .env: copy .env.example and fill in the Slack tokens"; exit 1; }
  grep -q '^SLACK_BOT_TOKEN=.' .env || echo "warning: SLACK_BOT_TOKEN is empty in .env: no DMs"
  grep -q '^NOTIFY_CHANNELS=.*slack' .env || echo "warning: NOTIFY_CHANNELS in .env lacks slack: no DMs"
  stop
  uv run python -m tools.reset_demo --preload
  mkdir -p "$DEMO_DIR/logs"
  run=(uv run --env-file .env)
  nohup "${run[@]}" python -m adjud.runner > "$DEMO_DIR/logs/runner.log" 2>&1 &
  echo $! >> "$PIDS"
  nohup "${run[@]}" streamlit run ui/app.py --server.headless=true --server.port=8501 \
    > "$DEMO_DIR/logs/ui.log" 2>&1 &
  echo $! >> "$PIDS"
  nohup "${run[@]}" python -m adjud.slack_app > "$DEMO_DIR/logs/slack.log" 2>&1 &
  echo $! >> "$PIDS"
  for _ in $(seq 60); do curl -s localhost:8501/_stcore/health > /dev/null && break; sleep 1; done
  curl -s localhost:8501/_stcore/health > /dev/null || { echo "The UI did not start: see $DEMO_DIR/logs/ui.log"; exit 1; }
  sleep 3
  grep -q "Bolt app is running" "$DEMO_DIR/logs/slack.log" \
    || echo "warning: the Slack app is not connected yet: see $DEMO_DIR/logs/slack.log"
  echo "Ready: UI http://localhost:8501 · logs $DEMO_DIR/logs · in Slack: /adj act-as rahul"
  echo "Import from Rahul's App Home within 5 minutes, or the runner imports by itself."
}

case "${1:-}" in
  start) start ;;
  stop) stop; echo "Stopped." ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
