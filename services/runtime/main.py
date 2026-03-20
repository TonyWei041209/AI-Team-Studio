"""AI Team Studio - Local Runtime Server."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_db_path
from routers import projects, tasks, agent_runs, approvals, logs, orchestration, tools
from routers import providers as providers_router, settings as settings_router
from routers import completion as completion_router
from routers import skills as skills_router
from routers import roles as roles_router
from routers import dashboard as dashboard_router
from routers.settings import load_and_apply_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    init_db()
    print(f"[runtime] Database initialized at {get_db_path()}")
    load_and_apply_settings()
    print("[runtime] Provider settings loaded")
    yield


app = FastAPI(
    title="AI Team Studio Runtime",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow the Tauri frontend (localhost dev server) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(agent_runs.router)
app.include_router(approvals.router)
app.include_router(logs.router)
app.include_router(orchestration.router)
app.include_router(tools.router)
app.include_router(providers_router.router)
app.include_router(settings_router.router)
app.include_router(completion_router.router)
app.include_router(skills_router.router)
app.include_router(roles_router.router)
app.include_router(dashboard_router.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint for frontend connectivity verification."""
    db_exists = get_db_path().exists()
    return {
        "status": "ok",
        "version": "0.1.0",
        "database": "connected" if db_exists else "missing",
    }


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("RUNTIME_PORT", "9800"))
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )
