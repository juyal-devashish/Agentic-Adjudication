#!/usr/bin/env bash
# Share the running Slack demo over two public links (Cloudflare quick tunnels): the app
# and the Mailpit inbox. Anyone with a link can open it; synthetic data only.
#
#   scripts/share-demo.sh start   open the two tunnels, then reset and start the demo
#                                 (scripts/slack-demo.sh) with Slack and e-mail links
#                                 pointing at the public app link; keeps the Mac awake
#   scripts/share-demo.sh stop    close the tunnels and stop the demo
#
# The links work only while this Mac is on, awake and online, with the demo running.
# Quick-tunnel links change every time the tunnels start. Needs: brew install cloudflared,
# and Mailpit running on :8025.
set -euo pipefail

cd "$(dirname "$0")/.."
DIR=data/share
PIDS="$DIR/share.pids"
mkdir -p "$DIR"

stop() {
  if [[ -f "$PIDS" ]]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
    rm -f "$PIDS"
  fi
}

tunnel() {  # $1 port, $2 name: prints the public https link
  local log="$DIR/$2.log"
  : > "$log"
  nohup cloudflared tunnel --no-autoupdate --url "http://localhost:$1" > "$log" 2>&1 &
  echo $! >> "$PIDS"
  for _ in $(seq 60); do
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | head -1 || true)
    [[ -n "$url" ]] && { echo "$url"; return; }
    sleep 1
  done
  echo "The $2 tunnel did not start: see $log" >&2
  exit 1
}

case "${1:-}" in
  start)
    command -v cloudflared > /dev/null || { echo "Install it first: brew install cloudflared"; exit 1; }
    curl -s localhost:8025 > /dev/null || echo "warning: Mailpit is not answering on :8025"
    stop
    app=$(tunnel 8501 app)
    mail=$(tunnel 8025 mail)
    APP_BASE_URL="$app" scripts/slack-demo.sh start
    nohup caffeinate -i -s > /dev/null 2>&1 &  # no idle sleep while sharing (on power)
    echo $! >> "$PIDS"
    printf 'app=%s\nmail=%s\n' "$app" "$mail" > "$DIR/links.txt"
    echo
    echo "Share these links (they work while this Mac is on, awake and online):"
    echo "  The app:   $app"
    echo "  E-mails:   $mail"
    ;;
  stop)
    stop
    scripts/slack-demo.sh stop
    ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
