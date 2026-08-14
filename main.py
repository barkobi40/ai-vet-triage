"""Local dev entrypoint. In production the ASGI app (app.main:app) is served
by uvicorn/gunicorn workers directly, not via this script."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        # Without this, uvicorn's file watcher defaults to the whole project
        # root — including .venv/ (~16k files) and any other virtualenv
        # someone happens to have sitting in the repo — which is real
        # overhead for the watcher and a plausible source of instability.
        # `app/` is the only directory whose changes actually need a reload;
        # web/dashboard.html is read fresh from disk on every request
        # already, so it doesn't need to be watched at all.
        reload_dirs=["app"],
    )
