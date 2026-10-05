"""Content negotiation core: header parsing, matching and variant selection.

The logic is deliberately framework-agnostic; :mod:`app.main` only wires it
into FastAPI.  All quality-factor arithmetic uses :class:`decimal.Decimal` so
that equal products compare exactly (e.g. 0.1 * 1.0 vs 0.5 * 0.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Union

# RFC 7230 token: 1*tchar (without whitespace, separators or '*').
_TOKEN_RE = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
# Permissive language tag: subtags of 1+ alphanumerics, joined by '-'
# (case-insensitive).  Prefix matching is done on the split subtag list.
_LANGUAGE_RE = r"[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*"
# q = 0..1, no leading/trailing dot, at most three fractional digits.
_Q_RE = r"(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?)"

MAX_VARIANTS = 30
MIN_VARIANTS = 1
MAX_HEADER_ITEMS = 16

Q_ONE = Decimal("1")
Q_ZERO = Decimal("0")


class NegotiationError(Exception):
    """Raised for 422 responses.

    ``loc`` identifies the offending value, e.g.
    ``["variant", 0, "media_type"]`` or ``["header", "accept", 2]``.
    Variant indices are 0-based; header item numbers are 1-based.
    """

    def __init__(self, msg: str, loc: list[Union[str, int]]):
        super().__init__(msg)
        self.msg = msg
        self.loc = loc

    def detail(self) -> dict[str, Any]:
        return {"loc": self.loc, "msg": self.msg}


def _variant_error(index: int, field: str, msg: str) -> NegotiationError:
    return NegotiationError(msg, ["variant", index, field])


def _header_error(header_name: str, item: Optional[int], msg: str) -> NegotiationError:
    loc: list[Union[str, int]] = ["header", header_name]
    if item is not None:
        loc.append(item)
    return NegotiationError(msg, loc)


@dataclass(frozen=True)
class MediaItem:
    raw: str
    type: str
    subtype: str  # "*" for type/*
    full_wildcard: bool  # */*
    q: Decimal
    index: int  # 1-based position in the header

    @property
    def specificity(self) -> int:
        """3 = exact, 2 = type/*, 1 = */*."""
        if self.full_wildcard:
            return 1
        if self.subtype == "*":
            return 2
        return 3


@dataclass(frozen=True)
class LanguageItem:
    raw: str
    subtags: tuple[str, ...]  # empty tuple means "*"
    q: Decimal
    index: int  # 1-based position in the header

    @property
    def is_wildcard(self) -> bool:
        return not self.subtags

    @property
    def specificity(self) -> int:
        """Number of matched subtags; the wildcard is least specific (-1)."""
        return -1 if self.is_wildcard else len(self.subtags)


@dataclass(frozen=True)
class Match:
    """Result of matching one axis."""

    q: Decimal
    item: Optional[int]  # 1-based header item, None for the synthesized default
    specificity: int


def _parse_params(segment: str, *, reject_extra: bool) -> Decimal:
    """Parse the ``;``-separated parameter list of one header item.

    Returns the effective q (default 1).  Raises :class:`ValueError` with an
    English message on any syntactic problem.
    """
    q: Optional[Decimal] = None
    for param in segment.split(";")[1:]:
        if "=" not in param:
            raise ValueError("parameter must be of the form q=<value>")
        key, value = param.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("empty parameter name or value")
        if key.lower() != "q":
            if reject_extra:
                raise ValueError(f"unsupported parameter {key!r}; only q is allowed")
            raise ValueError("unexpected parameter")
        if not re.fullmatch(_Q_RE, value):
            raise ValueError("q must be a number between 0 and 1 with at most 3 decimals")
        parsed = Decimal(value)
        if q is not None:
            raise ValueError("duplicate q parameter")
        q = parsed
    return q if q is not None else Q_ONE


def parse_media_item(
    raw: str,
    index: int,
    *,
    error_header: Optional[str] = None,
    error_index: Optional[int] = None,
    error_loc: Optional[list[Union[str, int]]] = None,
) -> MediaItem:
    """Parse one Accept item (e.g. ``text/html;q=0.8``)."""
    def fail(msg: str) -> NegotiationError:
        if error_loc is not None:
            return NegotiationError(msg, error_loc)
        if error_header is not None:
            return _header_error(error_header, error_index, msg)
        return NegotiationError(msg, ["<media>"])

    first = raw.split(";", 1)[0].strip()
    m = re.fullmatch(rf"({_TOKEN_RE}|\*)/({_TOKEN_RE}|\*)", first)
    if not m:
        raise fail("expected media type of the form type/subtype, type/* or */*")
    media_type, subtype = m.group(1), m.group(2)
    if media_type == "*" and subtype != "*":
        raise fail("a wildcard type requires a wildcard subtype (*/*)")
    try:
        q = _parse_params(raw, reject_extra=True)
    except ValueError as exc:
        raise fail(str(exc)) from exc
    return MediaItem(
        raw=raw,
        type=media_type.lower(),
        subtype=subtype.lower(),
        full_wildcard=media_type == "*",
        q=q,
        index=index,
    )


def parse_language_item(
    raw: str,
    index: int,
    *,
    error_header: Optional[str] = None,
    error_index: Optional[int] = None,
    error_loc: Optional[list[Union[str, int]]] = None,
) -> LanguageItem:
    """Parse one Accept-Language item (e.g. ``zh-CN;q=0.9`` or ``*``)."""
    def fail(msg: str) -> NegotiationError:
        if error_loc is not None:
            return NegotiationError(msg, error_loc)
        if error_header is not None:
            return _header_error(error_header, error_index, msg)
        return NegotiationError(msg, ["<language>"])

    first = raw.split(";", 1)[0].strip()
    if first == "*":
        subtags: tuple[str, ...] = ()
    elif re.fullmatch(_LANGUAGE_RE, first):
        subtags = tuple(part.lower() for part in first.split("-"))
    else:
        raise fail("expected a language tag (e.g. en, zh-CN) or *")
    try:
        q = _parse_params(raw, reject_extra=True)
    except ValueError as exc:
        raise fail(str(exc)) from exc
    return LanguageItem(raw=raw, subtags=subtags, q=q, index=index)


def _split_header(header_name: str, value: Optional[str]) -> Optional[list[str]]:
    """Split a header value on commas into stripped items.

    Returns None when the header is absent (use the default).  Raises a
    negotiation error on empty items or when more than 16 items are present.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _header_error(header_name, None, "header value must be a string")
    parts = [part.strip() for part in value.split(",")]
    if len(parts) > MAX_HEADER_ITEMS:
        raise _header_error(
            header_name,
            MAX_HEADER_ITEMS + 1,
            f"at most {MAX_HEADER_ITEMS} items are allowed",
        )
    for i, part in enumerate(parts, start=1):
        if not part:
            raise _header_error(header_name, i, "empty header item")
    return parts


def _parse_variant(variant: Any, index: int) -> tuple[str, MediaItem, tuple[str, ...]]:
    if not isinstance(variant, dict):
        raise _variant_error(index, "", "variant must be an object")
    variant_id = variant.get("id")
    if not isinstance(variant_id, str) or not variant_id:
        raise _variant_error(index, "id", "id must be a non-empty string")
    media_raw = variant.get("media_type")
    if not isinstance(media_raw, str) or not media_raw:
        raise _variant_error(index, "media_type", "media_type must be a non-empty string")
    language_raw = variant.get("language")
    if not isinstance(language_raw, str) or not language_raw:
        raise _variant_error(index, "language", "language must be a non-empty string")

    # Variants must be concrete: no parameters (not even q=1) and no wildcards.
    if ";" in media_raw:
        raise _variant_error(index, "media_type", "parameters are not allowed on variants")
    if ";" in language_raw:
        raise _variant_error(index, "language", "parameters are not allowed on variants")
    media = parse_media_item(
        media_raw, index, error_loc=["variant", index, "media_type"]
    )
    if media.full_wildcard or media.subtype == "*":
        raise _variant_error(index, "media_type", "wildcard media type is not allowed for a variant")

    language_item = parse_language_item(
        language_raw, index, error_loc=["variant", index, "language"]
    )
    if language_item.is_wildcard:
        raise _variant_error(index, "language", "wildcard language is not allowed for a variant")
    return variant_id, media, language_item.subtags


def _match_media(variant_media: MediaItem, items: list[MediaItem]) -> Match:
    best: Optional[MediaItem] = None
    for item in items:
        if item.full_wildcard:
            matches = True
        elif item.subtype == "*":
            matches = item.type == variant_media.type
        else:
            matches = item.type == variant_media.type and item.subtype == variant_media.subtype
        if not matches:
            continue
        # Most specific wins; at equal specificity the earlier item wins.
        if best is None or (item.specificity, -item.index) > (best.specificity, -best.index):
            best = item
    if best is None:
        return Match(q=Q_ZERO, item=None, specificity=0)
    return Match(q=best.q, item=best.index, specificity=best.specificity)


def _match_language(
    variant_subtags: tuple[str, ...], items: list[LanguageItem]
) -> Match:
    best: Optional[LanguageItem] = None
    for item in items:
        if item.is_wildcard:
            matches = True
            matched = 0
        else:
            # Prefix match that ends on a subtag boundary.
            matches = (
                len(item.subtags) <= len(variant_subtags)
                and variant_subtags[: len(item.subtags)] == item.subtags
            )
            matched = len(item.subtags)
        if not matches:
            continue
        if best is None:
            best = item
            continue
        # Wildcard is less specific than any concrete tag; at equal specificity
        # the earlier item wins.
        if item.is_wildcard and not best.is_wildcard:
            continue
        if not item.is_wildcard and best.is_wildcard:
            best = item
            continue
        if (matched, -item.index) > (
            (-1 if best.is_wildcard else len(best.subtags)),
            -best.index,
        ):
            best = item
    if best is None:
        return Match(q=Q_ZERO, item=None, specificity=-2)
    return Match(q=best.q, item=best.index, specificity=best.specificity)


def negotiate(payload: Any) -> dict[str, Any]:
    """Validate *payload*, select the best variant and return the response body.

    Raises :class:`NegotiationError` for 422 cases; the caller maps "no viable
    variant" to 406 by catching :class:`NoViableVariantError`.
    """
    if not isinstance(payload, dict):
        raise NegotiationError("request body must be a JSON object", ["body"])

    variants = payload.get("variants")
    if not isinstance(variants, list):
        raise NegotiationError("variants must be a list", ["variants"])
    if not (MIN_VARIANTS <= len(variants) <= MAX_VARIANTS):
        raise NegotiationError(
            f"variants must contain {MIN_VARIANTS}..{MAX_VARIANTS} items", ["variants"]
        )

    parsed_variants: list[tuple[str, MediaItem, tuple[str, ...]]] = []
    seen_ids: set[str] = set()
    for i, variant in enumerate(variants):
        variant_id, media, language_subtags = _parse_variant(variant, i)
        if variant_id in seen_ids:
            raise _variant_error(i, "id", "duplicate variant id")
        seen_ids.add(variant_id)
        parsed_variants.append((variant_id, media, language_subtags))

    accept_parts = _split_header("accept", payload.get("accept"))
    accept_lang_parts = _split_header("accept_language", payload.get("accept_language"))

    accept_items = (
        [parse_media_item(part, i, error_header="accept", error_index=i)
         for i, part in enumerate(accept_parts, start=1)]
        if accept_parts is not None
        else None
    )
    accept_lang_items = (
        [parse_language_item(part, i, error_header="accept_language", error_index=i)
         for i, part in enumerate(accept_lang_parts, start=1)]
        if accept_lang_parts is not None
        else None
    )

    # Default headers: */* (q=1) and * (q=1), synthesized with item None.
    default_media = Match(q=Q_ONE, item=None, specificity=MediaItem("", "*", "*", True, Q_ONE, 0).specificity)
    default_language = Match(q=Q_ONE, item=None, specificity=LanguageItem("", (), Q_ONE, 0).specificity)

    class Candidate:
        __slots__ = ("order", "variant_id", "media_match", "language_match")

        def __init__(self, order, variant_id, media_match, language_match):
            self.order = order
            self.variant_id = variant_id
            self.media_match = media_match
            self.language_match = language_match

    candidates: list[Candidate] = []
    for order, (variant_id, media, lang_subtags) in enumerate(parsed_variants):
        media_match = (
            _match_media(media, accept_items) if accept_items is not None else default_media
        )
        language_match = (
            _match_language(lang_subtags, accept_lang_items)
            if accept_lang_items is not None
            else default_language
        )
        # Both axes must be strictly positive.  A concrete q=0 match (most
        # specific) therefore removes the variant even if */* or * also match.
        if media_match.q > 0 and language_match.q > 0:
            candidates.append(Candidate(order, variant_id, media_match, language_match))

    if not candidates:
        raise NoViableVariantError()

    def sort_key(c: Candidate) -> tuple[Any, ...]:
        return (
            -(c.media_match.q * c.language_match.q),  # exact decimal product, desc
            -c.media_match.specificity,
            -c.language_match.specificity,
            c.order,
        )

    winner = min(candidates, key=sort_key)
    return {
        "id": winner.variant_id,
        "accept_q": float(winner.media_match.q),
        "accept_language_q": float(winner.language_match.q),
        "accept_item": winner.media_match.item,
        "accept_language_item": winner.language_match.item,
    }


class NoViableVariantError(Exception):
    """All variants were rejected (at least one axis matched q=0 or nothing)."""
