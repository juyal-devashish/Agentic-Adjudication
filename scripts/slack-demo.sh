#!/usr/bin/env bash
# The Slack demo (plan P-57, docs/slack-demo-runbook.md), with uv on macOS or Linux.
#
#   scripts/slack-demo.sh start   stop anything it started, reset the demo folder, start
#                                 the runner, the UI (:8501) and the Slack app
#   scripts/slack-demo.sh stop    stop them
#
# The demo lives in its own folder, data/demo by default (DEMO_DIR=... to change), so
# starting over never touches ./data. Settings and secrets come from .env (SLACK_*,
# NOTIFY_CHANNELS, SMTP_*, ANTHROPIC_API_KEY). The Quality Check agents run in replay
# mode (no API calls, the same answers every run). The Slack Agent (P-58) has its own
# mode: ADJUD_AGENT_MODE from .env if set, else live when .env has an ANTHROPIC_API_KEY,
# else off (the Agent then answers with the person's tasks and links, written by code).
# Start it no more than 3 minutes before presenting: the runner imports the queued AE
# file by itself 5 minutes after it starts, and the demo opens with that import in Slack.
set -euo pipefail

cd "$(dirname "$0")/.."
DEMO_DIR="${DEMO_DIR:-data/demo}"
PIDS="$DEMO_DIR/demo.pids"
export ADJUD_DATA_DIR="$DEMO_DIR" ADJUD_AI_MODE=replay

# The Slack Agent's AI mode. Reads only the ADJUD_AGENT_MODE line of .env and prints only
# the mode; the API key is only tested for being set, never read out.
agent_mode() {
  local mode
  mode=$(grep -E '^ADJUD_AGENT_MODE=' .env | tail -n 1 | cut -d= -f2- | tr -d "\"' \r" | tr '[:upper:]' '[:lower:]' || true)
  if [[ -z "$mode" ]]; then
    if grep -q '^ANTHROPIC_API_KEY=.' .env; then mode=live; else mode=off; fi
  fi
  case "$mode" in
    off | replay | live) ;;
    *) echo "warning: ADJUD_AGENT_MODE in .env is not off, replay or live: the Agent is off" >&2
       mode=off ;;
  esac
  echo "$mode"
}

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
  ADJUD_AGENT_MODE="$(agent_mode)"
  export ADJUD_AGENT_MODE  # exported values win over .env's in uv run --env-file
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
  echo "The Slack Agent's AI mode: $ADJUD_AGENT_MODE (set ADJUD_AGENT_MODE in .env to change)"
  echo "Import from Rahul's App Home within 5 minutes, or the runner imports by itself."
}

case "${1:-}" in
  start) start ;;
  stop) stop; echo "Stopped." ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
