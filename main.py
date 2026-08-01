import os
import sys
import ipaddress

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.main import app


def main_port() -> int:
    try:
        return int(os.getenv("SYNCANVAS_MAIN_PORT", "3000"))
    except (TypeError, ValueError):
        return 3000


def main_host() -> str:
    host = str(os.getenv("SYNCANVAS_MAIN_HOST", "127.0.0.1") or "").strip()
    if host.lower() == "localhost":
        return host
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise RuntimeError(
        "SynCanvas 当前仅允许绑定本机回环地址；请将 SYNCANVAS_MAIN_HOST 设置为 127.0.0.1。"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=main_host(),
        port=main_port(),
        backlog=int(os.getenv("SYNCANVAS_UVICORN_BACKLOG", "2048")),
        timeout_keep_alive=int(os.getenv("SYNCANVAS_UVICORN_KEEP_ALIVE", "10")),
    )
