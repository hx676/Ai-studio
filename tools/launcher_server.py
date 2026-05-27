from __future__ import annotations

import json
import os
import argparse
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import service_supervisor as supervisor


HOST = "127.0.0.1"
PORT = supervisor.LAUNCHER_PORT
START_LOCK = threading.Lock()
START_STATE: Dict = {"running": False, "last_result": None, "last_error": "", "last_started_at": ""}


def json_bytes(payload: Dict, status: int = 200) -> Tuple[int, bytes, str]:
    return status, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8"


def text_bytes(text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> Tuple[int, bytes, str]:
    return status, text.encode("utf-8"), content_type


def open_path(path: str) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return True
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(target)])
    return True


def run_start_worker() -> None:
    with START_LOCK:
        START_STATE.update({"running": True, "last_error": "", "last_started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        result = supervisor.start_services_once()
        with START_LOCK:
            START_STATE.update({"running": False, "last_result": result, "last_error": "" if result.get("ok") else "启动中有错误"})
    except Exception as exc:
        with START_LOCK:
            START_STATE.update({"running": False, "last_error": str(exc), "last_result": None})


def html_page() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SynCanvas 启动器</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #15171a;
      --muted: #68707d;
      --line: #dfe3e8;
      --ok: #0f8a5f;
      --warn: #b26a00;
      --bad: #c9332f;
      --run: #2563eb;
      --soft-ok: #e9f7f1;
      --soft-warn: #fff5df;
      --soft-bad: #fdebea;
      --soft-run: #eaf1ff;
      --shadow: 0 10px 28px rgba(23, 28, 38, .08);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    }

    header {
      background: #111827;
      color: #fff;
      padding: 22px 32px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
    }

    h1 {
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }

    .subtitle { color: #c9d2df; margin-top: 4px; }
    .root { color: #c9d2df; font-size: 12px; word-break: break-all; max-width: 720px; text-align: right; }

    main {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(360px, .8fr);
      gap: 18px;
      padding: 20px 32px 32px;
    }

    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .section-head {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }

    button, a.button {
      height: 36px;
      border: 1px solid #cfd6df;
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 0 13px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      font-weight: 600;
      min-width: 92px;
    }

    button.primary { background: #2563eb; border-color: #2563eb; color: white; }
    button.danger { background: #c9332f; border-color: #c9332f; color: white; }
    button:disabled { opacity: .55; cursor: not-allowed; }

    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      padding: 16px 18px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-height: 72px;
    }

    .metric .label { color: var(--muted); font-size: 12px; }
    .metric .value { margin-top: 6px; font-size: 24px; font-weight: 700; }
    .metric.ok .value { color: var(--ok); }
    .metric.warning .value { color: var(--warn); }
    .metric.error .value { color: var(--bad); }
    .metric.running .value { color: var(--run); }

    .services {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 0 18px 18px;
    }

    .service-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fff;
    }

    .service-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }

    .service-name { font-weight: 700; font-size: 15px; }
    .service-detail { color: var(--muted); font-size: 12px; }

    .badge {
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }

    .badge.ok { color: var(--ok); background: var(--soft-ok); }
    .badge.warning { color: var(--warn); background: var(--soft-warn); }
    .badge.error { color: var(--bad); background: var(--soft-bad); }
    .badge.running { color: var(--run); background: var(--soft-run); }
    .badge.idle { color: var(--muted); background: #eef1f4; }
    .badge.stopped { color: var(--muted); background: #eef1f4; }

    .check-groups { padding: 0 18px 18px; }
    .group { border-top: 1px solid var(--line); padding-top: 14px; margin-top: 14px; }
    .group:first-child { border-top: 0; padding-top: 0; margin-top: 0; }
    .group-title { font-weight: 700; margin-bottom: 8px; }
    .check-row {
      display: grid;
      grid-template-columns: 110px minmax(160px, .8fr) minmax(0, 1.2fr);
      gap: 10px;
      align-items: start;
      padding: 9px 0;
      border-top: 1px solid #edf0f3;
    }
    .check-row:first-of-type { border-top: 0; }
    .check-label { font-weight: 600; }
    .check-detail { color: var(--muted); word-break: break-all; }
    .suggestion { color: #444b55; margin-top: 4px; }

    .side-body { padding: 16px 18px 18px; }
    .log-controls { display: flex; gap: 8px; margin-bottom: 10px; }
    select {
      height: 36px;
      border: 1px solid #cfd6df;
      border-radius: 6px;
      background: white;
      padding: 0 10px;
    }
    pre {
      margin: 0;
      min-height: 360px;
      max-height: 560px;
      overflow: auto;
      background: #111827;
      color: #d8dee9;
      border-radius: 8px;
      padding: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.5 Consolas, "Courier New", monospace;
    }
    .small-note { color: var(--muted); font-size: 12px; margin-top: 10px; }

    @media (max-width: 980px) {
      header { display: block; }
      .root { text-align: left; margin-top: 8px; }
      main { grid-template-columns: 1fr; padding: 16px; }
      .summary, .services { grid-template-columns: 1fr; }
      .check-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>SynCanvas 启动器</h1>
      <div class="subtitle">启动前自检、三后台服务管理、日志查看</div>
    </div>
    <div class="root" id="rootPath">加载中...</div>
  </header>

  <main>
    <section>
      <div class="section-head">
        <h2>总览</h2>
        <span id="lastRefresh" class="service-detail">尚未刷新</span>
      </div>
      <div class="toolbar">
        <button class="primary" id="startBtn">启动全部</button>
        <button class="danger" id="stopBtn">停止本次启动服务</button>
        <button id="refreshBtn">刷新自检</button>
        <button id="openMainBtn">打开主应用</button>
        <button id="openLogsBtn">打开日志目录</button>
      </div>
      <div class="summary">
        <div class="metric ok"><div class="label">正常</div><div class="value" id="okCount">0</div></div>
        <div class="metric warning"><div class="label">警告</div><div class="value" id="warningCount">0</div></div>
        <div class="metric error"><div class="label">错误</div><div class="value" id="errorCount">0</div></div>
        <div class="metric running"><div class="label">启动中</div><div class="value" id="runningCount">0</div></div>
      </div>
      <div class="services" id="services"></div>
      <div class="section-head">
        <h2>深度自检</h2>
        <span class="service-detail">错误会阻止对应服务稳定启动，警告不一定阻止启动</span>
      </div>
      <div class="check-groups" id="diagnostics"></div>
    </section>

    <section>
      <div class="section-head">
        <h2>日志</h2>
        <span id="startState" class="service-detail">空闲</span>
      </div>
      <div class="side-body">
        <div class="log-controls">
          <select id="logService">
            <option value="main">主应用</option>
            <option value="tts">TTS</option>
            <option value="heygem">HeyGem</option>
          </select>
          <select id="logStream">
            <option value="stdout">输出日志</option>
            <option value="stderr">错误日志</option>
          </select>
          <button id="loadLogBtn">读取日志</button>
        </div>
        <pre id="logText">选择服务后读取日志。</pre>
        <div class="small-note">启动器只做诊断和引导，不会自动改系统环境或删除缓存。</div>
      </div>
    </section>
  </main>

  <script>
    const state = { mainUrl: 'http://127.0.0.1:3000/', logDir: '' };
    const STARTUP_POLL_MS = 5000;
    const STARTUP_POLL_LIMIT_MS = 7 * 60 * 1000;
    let startupPollTimer = null;
    let startupPollStartedAt = 0;
    const statusText = { ready: '就绪', starting: '预热中', partial: '部分就绪', stopped: '未启动' };
    const checkText = { ok: '正常', warning: '警告', error: '错误', running: '启动中', idle: '待启动' };

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { ok: false, text }; }
      if (!res.ok) throw new Error(data.detail || data.error || text || '请求失败');
      return data;
    }

    function badge(cls, text) {
      return `<span class="badge ${cls}">${text}</span>`;
    }

    function renderServices(services) {
      const el = document.getElementById('services');
      el.innerHTML = services.map(s => {
        const cls = s.state === 'ready' ? 'ok' : (s.state === 'starting' ? 'running' : (s.state === 'partial' ? 'warning' : 'stopped'));
        const source = s.source === 'managed' ? `启动器管理 PID ${s.pid}` : (s.source === 'external' ? '外部已运行' : (s.source === 'partial' ? '部分接口已运行' : (s.source === 'warming' ? '端口已打开，等待接口就绪' : '等待启动')));
        const checks = (s.checks || []).map(c => `${c.label}: ${c.ready ? 'ready' : (c.port_open ? '端口已开，等待接口' : '未连接')}`).join(' / ');
        return `<div class="service-card">
          <div class="service-top">
            <div>
              <div class="service-name">${s.label}</div>
              <div class="service-detail">${source}</div>
            </div>
            ${badge(cls, statusText[s.state] || s.state)}
          </div>
          <div class="service-detail">${checks}</div>
        </div>`;
      }).join('');
    }

    function renderDiagnostics(items) {
      const el = document.getElementById('diagnostics');
      const groups = {};
      for (const item of items) {
        (groups[item.group] ||= []).push(item);
      }
      el.innerHTML = Object.entries(groups).map(([name, rows]) => {
        return `<div class="group">
          <div class="group-title">${name}</div>
          ${rows.map(row => {
            const cls = row.status === 'ok' ? 'ok' : (row.status === 'running' ? 'running' : (row.status === 'idle' ? 'idle' : row.status));
            return `<div class="check-row">
              <div>${badge(cls, checkText[row.status] || row.status)}</div>
              <div class="check-label">${row.label}</div>
              <div class="check-detail">${row.detail || ''}${row.suggestion ? `<div class="suggestion">${row.suggestion}</div>` : ''}</div>
            </div>`;
          }).join('')}
        </div>`;
      }).join('');
    }

    function stopStartupPolling() {
      if (startupPollTimer) clearInterval(startupPollTimer);
      startupPollTimer = null;
      startupPollStartedAt = 0;
    }

    function beginStartupPolling() {
      stopStartupPolling();
      startupPollStartedAt = Date.now();
      // Startup-only polling; updateStartupPolling stops it when services are ready or the warmup window expires.
      startupPollTimer = setInterval(() => refreshStatus().catch(() => {}), STARTUP_POLL_MS);
    }

    function updateStartupPolling(data) {
      if (!startupPollStartedAt) return;
      const services = data.services || [];
      const start = data.start || {};
      const timedOut = Date.now() - startupPollStartedAt >= STARTUP_POLL_LIMIT_MS;
      const allReady = services.length > 0 && services.every(s => s.ready || s.state === 'ready');
      if (timedOut || allReady) stopStartupPolling();
    }

    async function refreshStatus() {
      const data = await api('/api/launcher/status');
      state.mainUrl = data.main_url;
      state.logDir = data.log_dir;
      document.getElementById('rootPath').textContent = data.root;
      document.getElementById('lastRefresh').textContent = `刷新时间 ${data.time}`;
      document.getElementById('okCount').textContent = data.counts.ok || 0;
      document.getElementById('warningCount').textContent = data.counts.warning || 0;
      document.getElementById('errorCount').textContent = data.counts.error || 0;
      document.getElementById('runningCount').textContent = data.counts.running || 0;
      renderServices(data.services || []);
      renderDiagnostics(data.diagnostics || []);
      const start = data.start || {};
      document.getElementById('startState').textContent = start.running ? '启动命令执行中' : (start.last_error || '空闲');
      document.getElementById('startBtn').disabled = !!start.running;
      updateStartupPolling(data);
      return data;
    }

    async function startAll() {
      document.getElementById('startState').textContent = '正在启动...';
      beginStartupPolling();
      try {
        await api('/api/launcher/start', { method: 'POST' });
        await refreshStatus();
      } catch (err) {
        stopStartupPolling();
        throw err;
      }
    }

    async function stopAll() {
      stopStartupPolling();
      await api('/api/launcher/stop', { method: 'POST' });
      await refreshStatus();
    }

    async function loadLog() {
      const service = document.getElementById('logService').value;
      const stream = document.getElementById('logStream').value;
      const data = await api(`/api/launcher/logs?service=${encodeURIComponent(service)}&stream=${encodeURIComponent(stream)}`);
      document.getElementById('logText').textContent = data.text || `暂无日志：${data.path || ''}`;
    }

    async function openTarget(target) {
      await api(`/api/launcher/open?target=${encodeURIComponent(target)}`, { method: 'POST' });
    }

    document.getElementById('openMainBtn').addEventListener('click', () => window.open(state.mainUrl, '_blank'));
    const showError = message => {
      const text = String(message || '操作失败');
      const box = document.getElementById('logText');
      if (box) box.textContent = text;
    };
    document.getElementById('startBtn').addEventListener('click', () => startAll().catch(e => showError(e.message)));
    document.getElementById('stopBtn').addEventListener('click', () => stopAll().catch(e => showError(e.message)));
    document.getElementById('refreshBtn').addEventListener('click', () => refreshStatus().catch(e => showError(e.message)));
    document.getElementById('openLogsBtn').addEventListener('click', () => openTarget('logs').catch(e => showError(e.message)));
    document.getElementById('loadLogBtn').addEventListener('click', () => loadLog().catch(e => showError(e.message)));

    refreshStatus().catch(e => showError(e.message));
  </script>
</body>
</html>
"""


class LauncherHandler(BaseHTTPRequestHandler):
    server_version = "SynCanvasLauncher/1.0"

    def send_payload(self, payload: Tuple[int, bytes, str]) -> None:
        status, body, content_type = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                self.send_payload(text_bytes(html_page(), content_type="text/html; charset=utf-8"))
            elif path == "/api/launcher/status":
                payload = supervisor.build_status(include_gpu=True)
                with START_LOCK:
                    payload["start"] = dict(START_STATE)
                self.send_payload(json_bytes(payload))
            elif path == "/api/launcher/logs":
                service = (query.get("service") or ["main"])[0]
                stream = (query.get("stream") or ["stdout"])[0]
                self.send_payload(json_bytes(supervisor.read_recent_log(service, stream)))
            else:
                self.send_payload(json_bytes({"ok": False, "detail": "Not found"}, 404))
        except Exception as exc:
            self.send_payload(json_bytes({"ok": False, "detail": str(exc)}, 500))

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/launcher/start":
                with START_LOCK:
                    if START_STATE.get("running"):
                        self.send_payload(json_bytes({"ok": True, "running": True, "detail": "启动命令已在执行"}))
                        return
                    START_STATE.update({"running": True, "last_error": "", "last_result": None})
                thread = threading.Thread(target=run_start_worker, daemon=True)
                thread.start()
                self.send_payload(json_bytes({"ok": True, "running": True}))
            elif parsed.path == "/api/launcher/stop":
                code = supervisor.stop_tracked_services()
                self.send_payload(json_bytes({"ok": code == 0, "code": code}, 200 if code == 0 else 500))
            elif parsed.path == "/api/launcher/open":
                target = (query.get("target") or [""])[0]
                if target == "logs":
                    ok = open_path(str(supervisor.LOG_DIR))
                elif target == "root":
                    ok = open_path(str(supervisor.BASE_DIR))
                else:
                    ok = False
                self.send_payload(json_bytes({"ok": ok}, 200 if ok else 404))
            else:
                self.send_payload(json_bytes({"ok": False, "detail": "Not found"}, 404))
        except Exception as exc:
            self.send_payload(json_bytes({"ok": False, "detail": str(exc)}, 500))

    def log_message(self, format: str, *args) -> None:
        print("[%s] %s" % (time.strftime("%H:%M:%S"), format % args))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SynCanvas local web launcher.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the launcher page automatically.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supervisor.ensure_dirs()
    url = f"http://{HOST}:{PORT}/"
    server = ThreadingHTTPServer((HOST, PORT), LauncherHandler)
    print(f"SynCanvas launcher: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLauncher stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
