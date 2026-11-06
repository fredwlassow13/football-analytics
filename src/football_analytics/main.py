import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from football_analytics.infrastructure.database import get_engine
from football_analytics.modules.competitions.router import router as competitions_router
from football_analytics.modules.matches.router import (
    router as matches_router,
)
from football_analytics.modules.providers.router import router as providers_router

logger = logging.getLogger(__name__)
app = FastAPI(title="Football Analytics", version="0.1.0")
app.include_router(providers_router)
app.include_router(matches_router)
app.include_router(competitions_router)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database request failed: %s", request.url.path, exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Indica se o processo da API está disponível."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    """Confirma que a API consegue consultar o PostgreSQL."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database readiness check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok", "database": "ok"}
