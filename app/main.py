"""FastAPI application exposing POST /negotiate."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .negotiation import NegotiationError, NoViableVariantError, negotiate

app = FastAPI(
    title="Local Content Negotiation API",
    version="1.0.0",
    description="Select the best content variant from Accept and Accept-Language values.",
)


class NegotiateResponse(BaseModel):
    id: str
    accept_q: float
    accept_language_q: float
    accept_item: Optional[int] = None
    accept_language_item: Optional[int] = None


@app.exception_handler(NegotiationError)
async def negotiation_error_handler(request: Request, exc: NegotiationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.detail()})


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"service": "content-negotiation", "docs": "/docs", "endpoint": "/negotiate"}


@app.post(
    "/negotiate",
    response_model=NegotiateResponse,
    responses={
        406: {"description": "No variant satisfies both axes with positive quality."},
        422: {"description": "Invalid request payload, variant, or header item."},
    },
    tags=["negotiation"],
)
async def negotiate_variants(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content={"detail": {"loc": ["body"], "msg": "request body must be valid JSON"}},
        )

    try:
        result = negotiate(payload)
    except NoViableVariantError:
        return JSONResponse(
            status_code=406,
            content={"detail": "No variant satisfies the Accept and Accept-Language criteria."},
        )
    return JSONResponse(status_code=200, content=result)
