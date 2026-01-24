#!/bin/bash
# Surgical start script for ActivityCopilot project
PROJECT_DIR="/home/andy/Dev/strava-activity-copilot"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"
VENV_UVICORN="$PROJECT_DIR/venv/bin/uvicorn"

echo "Surgically stopping project services on ports 8000 and 8001..."
fuser -k 8000/tcp 8001/tcp 2>/dev/null
sleep 2

echo "Starting MCP Server on port 8001..."
nohup $VENV_PYTHON "$PROJECT_DIR/mcp-server/src/strava_http_server.py" > "$PROJECT_DIR/mcp_new.log" 2>&1 &

echo "Starting Backend Server on port 8000..."
cd "$PROJECT_DIR"
nohup $VENV_UVICORN backend.main:app --host 127.0.0.1 --port 8000 --reload > "$PROJECT_DIR/backend_new.log" 2>&1 &

echo "Starting Frontend Dev Server on port 5173..."
fuser -k 5173/tcp 2>/dev/null
nohup npm --prefix "$PROJECT_DIR/frontend" run dev -- --host > "$PROJECT_DIR/frontend_new.log" 2>&1 &

sleep 5
echo "--- STATUS CHECK ---"
ps aux | grep -E "uvicorn|strava_http_server.py|vite" | grep "$PROJECT_DIR"
ss -tulpn | grep -E ":8000|:8001|:5173"
echo "--- LOGS ---"
tail -n 5 "$PROJECT_DIR/backend_new.log"
tail -n 5 "$PROJECT_DIR/mcp_new.log"
