import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.main import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=3000,
        backlog=int(os.getenv("SYNCANVAS_UVICORN_BACKLOG", "2048")),
        timeout_keep_alive=int(os.getenv("SYNCANVAS_UVICORN_KEEP_ALIVE", "10")),
    )
