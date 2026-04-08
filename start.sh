#!/bin/bash
# DeG£N$ — Single launch script
# Builds the UI then starts ONE server that handles both API + frontend

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║        DeG£N\$ — Starting Up         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Kill anything already on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null || true

# Python deps
echo "→ Checking Python dependencies..."
pip install -r requirements.txt -q

# Build the React frontend into ui/dist/ (always fresh — wipe old dist first)
echo "→ Building UI..."
cd "$ROOT/ui"
rm -rf dist                      # nuke old build so stale files can never linger
[ ! -d node_modules ] && npm install --silent
npm run build --silent
cd "$ROOT"

# Get local IP for phone access
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null \
  || ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)

echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │  ✓  http://localhost:8000       — this computer  │"
if [ -n "$LOCAL_IP" ]; then
echo "  │  ✓  http://$LOCAL_IP:8000  — phone / tablet   │"
echo "  │     (must be on the same WiFi)                  │"
fi
echo "  │                                                 │"
echo "  │  Password: Joseph992127!!!                       │"
echo "  │  Ctrl+C to stop                                 │"
echo "  └─────────────────────────────────────────────────┘"
echo ""

# Open browser on this machine
open "http://localhost:8000" 2>/dev/null || true

# Start server (bots launch automatically inside server.py)
python server.py
