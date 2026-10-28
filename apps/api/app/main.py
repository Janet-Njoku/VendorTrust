from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.routers import assessments, me, questionnaires, vendors


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    production = settings.environment == "production"

    app = FastAPI(
        title="VendorTrust API",
        version="0.0.1",
        # Interactive docs and the schema are not served in production.
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )

    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Org-Id"],
    )

    install_error_handlers(app)
    app.include_router(me.router)
    app.include_router(vendors.router)
    app.include_router(questionnaires.router)
    app.include_router(assessments.router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness probe. Deliberately exposes no version, config or dependency detail."""
        return {"status": "ok"}

    return app


app = create_app()
