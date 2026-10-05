"""Core content-negotiation logic: header parsing, matching and selection.

This module is framework-agnostic so it can be unit-tested without FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

MAX_HEADER_ITEMS = 16

# RFC 7230 token characters (used for media type / subtype).
_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]"
TOKEN_RE = re.compile(_TOKEN + r"+")
# Language tag: alpha{1,8} ("-" alphanum{1,8})*   (RFC 5646 simplified).
LANG_TAG_RE = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*")
# q value: 0, 0.xxx, 1, 1.000 — at most three decimals, range [0, 1].
Q_RE = re.compile(r"(?:0(?:\.[0-9]{1,3})?|1(?:\.0{1,3})?)")


class NegotiationError(Exception):
    """Invalid user input; ``detail`` is returned as the 422 response body."""

    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("message", "invalid input"))


def _header_error(header: str, item_index: Optional[int], message: str) -> NegotiationError:
    return NegotiationError(
        {
            "error": "invalid_header",
            "header": header,
            "item_index": item_index,
            "message": message,
        }
    )


@dataclass
class HeaderItem:
    """One parsed item of an Accept / Accept-Language header."""

    value: str  # normalized (lowercased) range, e.g. "text/*" or "en-us"
    q: float
    index: Optional[int]  # position in the header; None for defaulted headers


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_params(parts: list[str], header: str, idx: int) -> float:
    """Parse the ``;``-separated parameters of one item; only ``q`` is allowed."""
    q = 1.0
    seen_q = False
    for part in parts:
        part = part.strip()
        if not part:
            raise _header_error(header, idx, "empty parameter")
        name, sep, raw_val = part.partition("=")
        name = name.strip()
        raw_val = raw_val.strip()
        if name.lower() != "q":
            raise _header_error(
                header, idx, f"unsupported parameter {name!r}: only 'q' is allowed"
            )
        if not sep:
            raise _header_error(header, idx, "missing q value")
        if seen_q:
            raise _header_error(header, idx, "duplicate q parameter")
        seen_q = True
        if not Q_RE.fullmatch(raw_val):
            raise _header_error(
                header,
                idx,
                f"invalid q value {raw_val!r}: must be between 0 and 1 "
                "with at most three decimal places",
            )
        q = float(raw_val)
    return q


def _validate_media_range(value: str, header: str, idx: int) -> None:
    if "/" not in value:
        raise _header_error(
            header,
            idx,
            f"invalid media range {value!r}: expected type/subtype, type/* or */*",
        )
    type_, _, subtype = value.partition("/")
    if "/" in subtype:
        raise _header_error(header, idx, f"invalid media range {value!r}")
    if type_ == "*":
        if subtype != "*":
            raise _header_error(
                header, idx, f"invalid media range {value!r}: '*' type requires '*' subtype"
            )
        return  # */*
    if not TOKEN_RE.fullmatch(type_) or "*" in type_:
        raise _header_error(header, idx, f"invalid type in media range {value!r}")
    if subtype == "*":
        return  # type/*
    if not TOKEN_RE.fullmatch(subtype) or "*" in subtype:
        raise _header_error(header, idx, f"invalid subtype in media range {value!r}")


def _validate_language_range(value: str, header: str, idx: int) -> None:
    if value == "*":
        return
    if not LANG_TAG_RE.fullmatch(value):
        raise _header_error(
            header, idx, f"invalid language range {value!r}: expected a language tag or '*'"
        )


def _parse_header(header_value: str, header_name: str, validate) -> list[HeaderItem]:
    raw_items = header_value.split(",")
    if len(raw_items) > MAX_HEADER_ITEMS:
        raise _header_error(
            header_name,
            None,
            f"too many items: {len(raw_items)} (maximum is {MAX_HEADER_ITEMS})",
        )
    items: list[HeaderItem] = []
    for idx, raw in enumerate(raw_items):
        raw = raw.strip()
        if not raw:
            raise _header_error(header_name, idx, "empty item")
        parts = raw.split(";")
        value = parts[0].strip()
        if not value:
            raise _header_error(header_name, idx, "empty value")
        validate(value, header_name, idx)
        q = _parse_params(parts[1:], header_name, idx)
        items.append(HeaderItem(value=value.lower(), q=q, index=idx))
    return items


def parse_accept(header_value: str) -> list[HeaderItem]:
    return _parse_header(header_value, "accept", _validate_media_range)


def parse_accept_language(header_value: str) -> list[HeaderItem]:
    return _parse_header(header_value, "accept_language", _validate_language_range)


def default_accept() -> list[HeaderItem]:
    """Implicit ``Accept: */*`` (q=1, no header item index)."""
    return [HeaderItem(value="*/*", q=1.0, index=None)]


def default_accept_language() -> list[HeaderItem]:
    """Implicit ``Accept-Language: *`` (q=1, no header item index)."""
    return [HeaderItem(value="*", q=1.0, index=None)]


# ---------------------------------------------------------------------------
# Variant validation
# ---------------------------------------------------------------------------

def is_specific_media_type(value: str) -> bool:
    """True for concrete ``type/subtype`` (no wildcards)."""
    if "/" not in value:
        return False
    type_, _, subtype = value.partition("/")
    if "/" in subtype or not type_ or not subtype:
        return False
    if "*" in type_ or "*" in subtype:
        return False
    return bool(TOKEN_RE.fullmatch(type_) and TOKEN_RE.fullmatch(subtype))


def is_language_tag(value: str) -> bool:
    return bool(LANG_TAG_RE.fullmatch(value))


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class AxisMatch:
    q: float
    index: Optional[int]
    specificity: int


def match_media(variant_media: str, items: list[HeaderItem]) -> Optional[AxisMatch]:
    """Most specific matching media range for a concrete ``type/subtype``.

    Specificity: exact (2) > type/* (1) > */* (0); ties keep the earliest
    header item. Returns None when nothing matches.
    """
    vtype, vsubtype = variant_media.lower().split("/")
    best: Optional[AxisMatch] = None
    for item in items:
        itype, isubtype = item.value.split("/")
        if itype == "*":
            spec = 0
        elif isubtype == "*":
            if itype != vtype:
                continue
            spec = 1
        else:
            if itype != vtype or isubtype != vsubtype:
                continue
            spec = 2
        if best is None or spec > best.specificity:
            best = AxisMatch(q=item.q, index=item.index, specificity=spec)
    return best


def match_language(variant_lang: str, items: list[HeaderItem]) -> Optional[AxisMatch]:
    """Most specific matching language range for a concrete language tag.

    A range matches when its subtags are a prefix of the tag's subtags
    (subtag-boundary match); specificity is the number of range subtags,
    ``*`` matches with specificity 0. Ties keep the earliest header item.
    Returns None when nothing matches.
    """
    vtags = variant_lang.lower().split("-")
    best: Optional[AxisMatch] = None
    for item in items:
        if item.value == "*":
            spec = 0
        else:
            rtags = item.value.split("-")
            if len(rtags) > len(vtags) or vtags[: len(rtags)] != rtags:
                continue
            spec = len(rtags)
        if best is None or spec > best.specificity:
            best = AxisMatch(q=item.q, index=item.index, specificity=spec)
    return best


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

@dataclass
class Selection:
    id: str
    media_q: float
    language_q: float
    media_index: Optional[int]
    language_index: Optional[int]


def negotiate(variants, accept_items: list[HeaderItem],
              lang_items: list[HeaderItem]) -> Optional[Selection]:
    """Pick the winning variant or return None (caller maps that to 406).

    Keeps variants whose media and language q are both > 0, then orders by
    q product (desc), media specificity (desc), language specificity (desc)
    and input order (asc).
    """
    best = None
    best_key = None
    for i, variant in enumerate(variants):
        media = match_media(variant.media_type, accept_items)
        lang = match_language(variant.language, lang_items)
        media_q = media.q if media else 0.0
        lang_q = lang.q if lang else 0.0
        if media_q <= 0.0 or lang_q <= 0.0:
            continue
        key = (
            -(media_q * lang_q),
            -(media.specificity if media else 0),
            -(lang.specificity if lang else 0),
            i,
        )
        if best_key is None or key < best_key:
            best_key = key
            best = Selection(
                id=variant.id,
                media_q=media_q,
                language_q=lang_q,
                media_index=media.index if media else None,
                language_index=lang.index if lang else None,
            )
    return best
