"""Local dev entrypoint. In production the ASGI app (app.main:app) is served
by uvicorn/gunicorn workers directly, not via this script."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
