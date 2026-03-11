"""AI Team Studio - Local Runtime Server."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_db_path


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
