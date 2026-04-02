from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from .config import AppConfig
from .service import IngestService


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    graph: bool = True


def create_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service = IngestService(config)
        _app.state.ingest_service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="memory-ingest", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "healthy": True,
            "mem0_user_id": config.mem0.user_id,
            "mem0_app_id": config.mem0.app_id,
        }

    @app.post("/query")
    async def query(body: QueryRequest) -> dict:
        service: IngestService = app.state.ingest_service
        result = service.query(body.query, top_k=body.top_k, enable_graph=body.graph)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return dict(result)

    return app
