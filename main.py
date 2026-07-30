import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

import httpx

from auth.router import router as auth_router
from router.chat import router as chat_router
from router.config import router as config_router
from router.user_providers import router as user_providers_router
from services.rag_service import RAGQueryService
from services.registry import ServiceRegistry
from services.loader import load_services_from_yaml

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
import logging

logger = logging.getLogger("backend")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from fastapi import HTTPException as FastAPIHTTPException
    if isinstance(exc, FastAPIHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    # Unexpected exception: log full traceback server-side, but never leak
    # internals (traceback, paths, key fragments) to the client.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Routers (API first) ─────────────────────────────────────────────────

app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(user_providers_router, prefix="/api")


# ── droneImaging 反向代理（/api/drone → {DRONE_IMAGING_BASE}/api）────────
# 前端统一通过 /api/drone/... 访问影像服务：开发模式由 Vite 代理转发，
# 生产模式（本服务直接提供前端页面）则由这里转发，保证两种模式行为一致。
# 地址通过环境变量 DRONE_IMAGING_BASE 配置，Docker 部署时指向容器服务名，
# 例如 http://drone-imaging:8002，避免硬编码 localhost。
DRONE_IMAGING_BASE = os.environ.get("DRONE_IMAGING_BASE", "http://localhost:8002")


@app.api_route("/api/drone/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def drone_proxy(path: str, request: Request):
    target = f"{DRONE_IMAGING_BASE}/{path}"
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method=request.method,
                url=target,
                params=request.query_params,
                content=body if body else None,
                headers={"Content-Type": request.headers.get("content-type", "application/json")},
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type"),
        )
    except httpx.RequestError as e:
        return JSONResponse(status_code=502, content={"detail": f"droneImaging 服务不可达: {e}"})


# ── SPA: serve static files + catch-all for Vue Router paths ────────────

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    # Mount static files (js, css, images, etc.)
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    # File extensions that are real static assets — a missing one should 404
    # rather than return index.html (L5), so the browser/devtools can
    # distinguish "route not found" from "static file not found".
    _STATIC_EXT = {
        ".js", ".css", ".map",  # scripts / styles / source maps
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",  # images
        ".woff", ".woff2", ".ttf", ".eot",  # fonts
        ".json", ".yaml", ".yml",  # data files
        ".txt", ".xml", ".webmanifest",
    }

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve index.html for all non-API paths (Vue Router SPA)."""
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        # Let genuine static-asset misses 404 instead of returning HTML.
        if "." in (full_path.rsplit("/", 1)[-1] if "/" in full_path else full_path):
            ext = "." + full_path.rsplit(".", 1)[-1].lower()
            if ext in _STATIC_EXT:
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
    from db.session import engine
    from db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Register built-in services
    registry = ServiceRegistry()
    registry.register(RAGQueryService())

    # Load custom services from services.yaml
    count = load_services_from_yaml()
    print(f"[startup] Registered {registry.count} service(s) ({count} from services.yaml)")
