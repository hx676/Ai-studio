#!/bin/bash
set -u

SUPPORT_ROOT="$HOME/Library/Application Support/SynCanvas"
PID_FILE="$SUPPORT_ROOT/service.pid"

if [ ! -f "$PID_FILE" ]; then
    /usr/bin/osascript -e 'display notification "没有发现正在运行的服务。" with title "SynCanvas"' >/dev/null 2>&1 || true
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -n "$PID" ] && /bin/kill -0 "$PID" 2>/dev/null; then
    /bin/kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
        /bin/kill -0 "$PID" 2>/dev/null || break
        sleep 0.2
    done
fi
rm -f "$PID_FILE"
/usr/bin/osascript -e 'display notification "服务已停止。" with title "SynCanvas"' >/dev/null 2>&1 || true
