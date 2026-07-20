from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.router import router as auth_router
from backend.router.chat import router as chat_router
from backend.router.config import router as config_router
from backend.router.user_providers import router as user_providers_router
from backend.services.rag_service import RAGQueryService
from backend.services.registry import ServiceRegistry
from backend.services.loader import load_services_from_yaml

app = FastAPI(title="phenomicsAgent API", version="0.3.0")


# ── CORS ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handler ────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi import HTTPException as FastAPIHTTPException
    if isinstance(exc, FastAPIHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    raise exc


# ── Routers (API first) ─────────────────────────────────────────────────

app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(user_providers_router, prefix="/api")


# ── SPA: serve static files + catch-all for Vue Router paths ────────────

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    # Mount static files (js, css, images, etc.)
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve index.html for all non-API paths (Vue Router SPA)."""
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    async def no_frontend():
        return JSONResponse(
            status_code=404,
            content={"detail": "Frontend not built. Run `cd frontend && npm run build`."},
        )


# ── Startup ──────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Initialize database tables and register services on startup."""
    from backend.db.session import engine
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Register built-in services
    registry = ServiceRegistry()
    registry.register(RAGQueryService())

    # Load custom services from services.yaml
    count = load_services_from_yaml()
    print(f"[startup] Registered {registry.count} service(s) ({count} from services.yaml)")
