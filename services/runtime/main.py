"""AI Team Studio - Local Runtime Server."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_db_path
from routers import projects, tasks, agent_runs, approvals, logs, orchestration, tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    init_db()
    print(f"[runtime] Database initialized at {get_db_path()}")
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
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=9800,
        reload=True,
    )
