"""Unit tests for the negotiation logic and the HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.negotiation import (
    HeaderItem,
    NegotiationError,
    match_language,
    match_media,
    parse_accept,
    parse_accept_language,
)

client = TestClient(app)


def post(payload):
    return client.post("/negotiate", json=payload)


def variants(*specs):
    return [
        {"id": vid, "media_type": mt, "language": lang} for vid, mt, lang in specs
    ]


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

class TestParseAccept:
    def test_simple(self):
        items = parse_accept("text/html, application/json;q=0.8, */*;q=0.1")
        assert [i.value for i in items] == ["text/html", "application/json", "*/*"]
        assert [i.q for i in items] == [1.0, 0.8, 0.1]
        assert [i.index for i in items] == [0, 1, 2]

    def test_case_insensitive_and_whitespace(self):
        items = parse_accept("  Text/HTML ; Q=0.5 ")
        assert items[0].value == "text/html"
        assert items[0].q == 0.5

    def test_type_wildcard(self):
        assert parse_accept("image/*")[0].value == "image/*"

    def test_empty_item_rejected(self):
        with pytest.raises(NegotiationError) as exc:
            parse_accept("text/html,,application/json")
        assert exc.value.detail["item_index"] == 1
        assert exc.value.detail["header"] == "accept"

    def test_trailing_comma_rejected(self):
        with pytest.raises(NegotiationError):
            parse_accept("text/html,")

    def test_empty_string_rejected(self):
        with pytest.raises(NegotiationError):
            parse_accept("")

    def test_too_many_items(self):
        with pytest.raises(NegotiationError) as exc:
            parse_accept(",".join(["text/html"] * 17))
        assert exc.value.detail["item_index"] is None

    def test_exactly_16_items_ok(self):
        assert len(parse_accept(",".join(["text/html"] * 16))) == 16

    @pytest.mark.parametrize(
        "value",
        ["text", "*/json", "*", "text/", "/html", "text/*/*", "text/ht ml"],
    )
    def test_invalid_media_ranges(self, value):
        with pytest.raises(NegotiationError):
            parse_accept(value)

    def test_unsupported_parameter(self):
        with pytest.raises(NegotiationError) as exc:
            parse_accept("text/html;charset=utf-8")
        assert exc.value.detail["item_index"] == 0

    def test_extra_parameter_after_q(self):
        with pytest.raises(NegotiationError):
            parse_accept("text/html;q=0.5;level=1")

    def test_duplicate_q(self):
        with pytest.raises(NegotiationError):
            parse_accept("text/html;q=0.5;q=0.6")

    def test_q_without_value(self):
        with pytest.raises(NegotiationError):
            parse_accept("text/html;q")

    def test_trailing_semicolon_rejected(self):
        with pytest.raises(NegotiationError):
            parse_accept("text/html;")

    @pytest.mark.parametrize("q", ["1.5", "2", "0.1234", ".5", "q", "", "-0.1", "01"])
    def test_invalid_q_values(self, q):
        with pytest.raises(NegotiationError):
            parse_accept(f"text/html;q={q}")

    @pytest.mark.parametrize(
        "q,expected",
        [("0", 0.0), ("0.0", 0.0), ("0.000", 0.0), ("0.125", 0.125),
         ("1", 1.0), ("1.0", 1.0), ("1.000", 1.0)],
    )
    def test_valid_q_values(self, q, expected):
        assert parse_accept(f"text/html;q={q}")[0].q == expected


class TestParseAcceptLanguage:
    def test_simple(self):
        items = parse_accept_language("en-US, en;q=0.9, *;q=0.1")
        assert [i.value for i in items] == ["en-us", "en", "*"]
        assert [i.q for i in items] == [1.0, 0.9, 0.1]

    def test_invalid_range(self):
        with pytest.raises(NegotiationError) as exc:
            parse_accept_language("en_US")
        assert exc.value.detail["header"] == "accept_language"

    def test_empty_item(self):
        with pytest.raises(NegotiationError):
            parse_accept_language("en, ")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

class TestMediaMatching:
    def items(self, header):
        return parse_accept(header)

    def test_exact_beats_wildcards(self):
        m = match_media("text/html", self.items("*/*;q=0.9, text/*;q=0.8, text/html;q=0.7"))
        assert (m.q, m.index, m.specificity) == (0.7, 2, 2)

    def test_type_wildcard_beats_star(self):
        m = match_media("text/html", self.items("*/*;q=0.9, text/*;q=0.8"))
        assert (m.q, m.index, m.specificity) == (0.8, 1, 1)

    def test_earliest_item_wins_ties(self):
        m = match_media("text/html", self.items("text/html;q=0.4, text/html;q=0.9"))
        assert (m.q, m.index) == (0.4, 0)

    def test_no_match(self):
        assert match_media("image/png", self.items("text/html, application/*")) is None

    def test_case_insensitive(self):
        m = match_media("Text/HTML", self.items("tExT/hTmL;q=0.3"))
        assert m.q == 0.3

    def test_specific_q_zero_not_overridden(self):
        m = match_media("text/html", self.items("text/html;q=0, */*;q=1"))
        assert m.q == 0.0


class TestLanguageMatching:
    def items(self, header):
        return parse_accept_language(header)

    def test_prefix_match_at_subtag_boundary(self):
        m = match_language("en-US", self.items("en;q=0.8"))
        assert (m.q, m.specificity) == (0.8, 1)

    def test_longer_prefix_wins(self):
        m = match_language("zh-Hans-CN", self.items("zh;q=0.5, zh-Hans;q=0.7"))
        assert (m.q, m.index, m.specificity) == (0.7, 1, 2)

    def test_no_boundary_no_match(self):
        # "en-u" is not a subtag-boundary prefix of "en-US"
        assert match_language("en-US", self.items("en-u")) is None

    def test_star_matches_everything(self):
        m = match_language("de-DE", self.items("en, *;q=0.2"))
        assert (m.q, m.specificity) == (0.2, 0)

    def test_no_match(self):
        assert match_language("fr", self.items("en, de")) is None

    def test_earliest_item_wins_ties(self):
        m = match_language("en", self.items("en;q=0.3, EN;q=0.9"))
        assert (m.q, m.index) == (0.3, 0)


# ---------------------------------------------------------------------------
# API: happy paths
# ---------------------------------------------------------------------------

class TestNegotiate:
    def test_defaults_when_headers_absent(self):
        r = post({"variants": variants(("a", "text/html", "en"))})
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "id": "a",
            "media_q": 1.0,
            "language_q": 1.0,
            "media_index": None,
            "language_index": None,
        }

    def test_highest_q_product_wins(self):
        r = post(
            {
                "variants": variants(
                    ("low", "text/html", "en"),
                    ("high", "application/json", "en"),
                ),
                "accept": "text/html;q=0.5, application/json;q=0.9",
                "accept_language": "en",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "high"
        assert r.json()["media_q"] == 0.9
        assert r.json()["media_index"] == 1
        assert r.json()["language_index"] == 0

    def test_specific_q_zero_excludes_variant(self):
        # text/html is explicitly q=0; */* must not revive it.
        r = post(
            {
                "variants": variants(
                    ("html", "text/html", "en"),
                    ("json", "application/json", "en"),
                ),
                "accept": "text/html;q=0, */*;q=0.5",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "json"

    def test_language_q_zero_excludes_variant(self):
        r = post(
            {
                "variants": variants(
                    ("en", "text/html", "en"),
                    ("de", "text/html", "de"),
                ),
                "accept_language": "en;q=0, *;q=0.4",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "de"

    def test_tie_break_media_specificity(self):
        r = post(
            {
                "variants": variants(
                    ("wild", "image/png", "en"),   # matches via */*
                    ("exact", "text/html", "en"),  # matches exactly
                ),
                "accept": "text/html, */*;q=1",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "exact"

    def test_tie_break_language_specificity(self):
        r = post(
            {
                "variants": variants(
                    ("generic", "text/html", "en"),      # matches "en" (1 subtag)
                    ("specific", "text/html", "en-US"),  # matches "en-us" (2 subtags)
                ),
                "accept_language": "en-us, en;q=1",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "specific"

    def test_tie_break_input_order(self):
        r = post(
            {
                "variants": variants(
                    ("first", "text/html", "en"),
                    ("second", "text/html", "en"),
                )
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "first"

    def test_language_prefix_selects_regional_variant(self):
        r = post(
            {
                "variants": variants(
                    ("plain", "text/html", "en"),
                    ("british", "text/html", "en-GB"),
                ),
                "accept_language": "en-GB;q=0.9, en;q=0.8",
            }
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "british"
        assert body["language_q"] == 0.9
        assert body["language_index"] == 0

    def test_case_insensitive_end_to_end(self):
        r = post(
            {
                "variants": variants(("a", "TEXT/HTML", "EN-us")),
                "accept": "text/HTML",
                "accept_language": "en-US",
            }
        )
        assert r.status_code == 200
        assert r.json()["id"] == "a"


# ---------------------------------------------------------------------------
# API: 406
# ---------------------------------------------------------------------------

class TestNotAcceptable:
    def test_no_media_match(self):
        r = post(
            {"variants": variants(("a", "image/png", "en")), "accept": "text/html"}
        )
        assert r.status_code == 406
        assert r.json()["detail"]["error"] == "no_acceptable_variant"

    def test_no_language_match(self):
        r = post(
            {
                "variants": variants(("a", "text/html", "fr")),
                "accept_language": "en, de",
            }
        )
        assert r.status_code == 406

    def test_all_q_zero(self):
        r = post(
            {
                "variants": variants(("a", "text/html", "en")),
                "accept": "*/*;q=0",
            }
        )
        assert r.status_code == 406


# ---------------------------------------------------------------------------
# API: 422
# ---------------------------------------------------------------------------

class TestValidation:
    def test_variant_index_reported(self):
        r = post({"variants": variants(("a", "text/*", "en"))})
        assert r.status_code == 422
        assert r.json()["detail"]["variant_index"] == 0

    def test_variant_wildcard_media_rejected(self):
        r = post({"variants": variants(("a", "*/*", "en"))})
        assert r.status_code == 422

    def test_variant_bad_language(self):
        r = post({"variants": variants(("a", "text/html", "en_US"))})
        assert r.status_code == 422
        assert r.json()["detail"]["variant_index"] == 0

    def test_duplicate_id(self):
        r = post(
            {
                "variants": variants(
                    ("a", "text/html", "en"), ("a", "image/png", "de")
                )
            }
        )
        assert r.status_code == 422
        assert r.json()["detail"]["variant_index"] == 1

    def test_header_error_reports_name_and_index(self):
        r = post(
            {
                "variants": variants(("a", "text/html", "en")),
                "accept": "text/html, bogus, application/json",
            }
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["header"] == "accept"
        assert detail["item_index"] == 1

    def test_accept_language_error(self):
        r = post(
            {
                "variants": variants(("a", "text/html", "en")),
                "accept_language": "en;q=2",
            }
        )
        assert r.status_code == 422
        assert r.json()["detail"]["header"] == "accept_language"
        assert r.json()["detail"]["item_index"] == 0

    def test_empty_variants(self):
        r = post({"variants": []})
        assert r.status_code == 422

    def test_too_many_variants(self):
        r = post(
            {"variants": variants(*[(f"v{i}", "text/html", "en") for i in range(31)])}
        )
        assert r.status_code == 422

    def test_exactly_30_variants_ok(self):
        r = post(
            {"variants": variants(*[(f"v{i}", "text/html", "en") for i in range(30)])}
        )
        assert r.status_code == 200

    def test_missing_variant_fields(self):
        r = post({"variants": [{"id": "a"}]})
        assert r.status_code == 422

    def test_too_many_header_items(self):
        r = post(
            {
                "variants": variants(("a", "text/html", "en")),
                "accept": ",".join(["text/html"] * 17),
            }
        )
        assert r.status_code == 422
        assert r.json()["detail"]["header"] == "accept"

    def test_empty_accept_string_rejected(self):
        r = post(
            {"variants": variants(("a", "text/html", "en")), "accept": ""}
        )
        assert r.status_code == 422
