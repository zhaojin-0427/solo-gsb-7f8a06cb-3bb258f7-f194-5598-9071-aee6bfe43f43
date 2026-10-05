"""FastAPI application exposing POST /negotiate."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .negotiation import (
    NegotiationError,
    default_accept,
    default_accept_language,
    is_language_tag,
    is_specific_media_type,
    negotiate,
    parse_accept,
    parse_accept_language,
)

app = FastAPI(
    title="Content Variant Negotiation API",
    version="1.0.0",
    summary="Selects the best content variant for given Accept / Accept-Language headers.",
)


class Variant(BaseModel):
    id: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    language: str = Field(min_length=1)


class NegotiateRequest(BaseModel):
    variants: list[Variant] = Field(min_length=1, max_length=30)
    accept: Optional[str] = None
    accept_language: Optional[str] = None


class NegotiateResponse(BaseModel):
    id: str
    media_q: float
    language_q: float
    media_index: Optional[int]
    language_index: Optional[int]


def _variant_error(index: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"error": "invalid_variant", "variant_index": index, "message": message},
    )


def _validate_variants(variants: list[Variant]) -> None:
    seen: dict[str, int] = {}
    for i, variant in enumerate(variants):
        if variant.id in seen:
            raise _variant_error(
                i,
                f"duplicate id {variant.id!r} (first occurrence at index {seen[variant.id]})",
            )
        seen[variant.id] = i
        if not is_specific_media_type(variant.media_type):
            raise _variant_error(
                i,
                f"invalid media_type {variant.media_type!r}: "
                "expected a specific type/subtype (no wildcards)",
            )
        if not is_language_tag(variant.language):
            raise _variant_error(
                i, f"invalid language {variant.language!r}: expected a language tag"
            )


@app.post("/negotiate", response_model=NegotiateResponse)
def negotiate_endpoint(req: NegotiateRequest) -> NegotiateResponse:
    _validate_variants(req.variants)

    try:
        accept_items = (
            parse_accept(req.accept) if req.accept is not None else default_accept()
        )
        lang_items = (
            parse_accept_language(req.accept_language)
            if req.accept_language is not None
            else default_accept_language()
        )
    except NegotiationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc

    selection = negotiate(req.variants, accept_items, lang_items)
    if selection is None:
        raise HTTPException(
            status_code=406,
            detail={
                "error": "no_acceptable_variant",
                "message": "no variant is acceptable for the given headers",
            },
        )
    return NegotiateResponse(**selection.__dict__)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
