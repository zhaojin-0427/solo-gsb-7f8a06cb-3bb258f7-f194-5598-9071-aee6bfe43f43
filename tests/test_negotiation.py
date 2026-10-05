import unittest

from app.negotiation import NegotiationError, NoViableVariantError, negotiate


def v(vid, media, lang):
    return {"id": vid, "media_type": media, "language": lang}


class NegotiateTests(unittest.TestCase):
    # ---- successful selection ------------------------------------------------

    def test_default_headers_choose_first_variant_with_null_items(self):
        body = {"variants": [v("a", "text/html", "en"), v("b", "application/json", "fr")]}
        r = negotiate(body)
        self.assertEqual(r["id"], "a")
        self.assertEqual(r["accept_q"], 1.0)
        self.assertEqual(r["accept_language_q"], 1.0)
        self.assertIsNone(r["accept_item"])
        self.assertIsNone(r["accept_language_item"])

    def test_exact_beats_type_wildcard_and_full_wildcard(self):
        body = {
            "variants": [v("html", "text/html", "en")],
            "accept": "*/*;q=0.1, text/*;q=0.5, text/html;q=0.9",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "html")
        self.assertAlmostEqual(r["accept_q"], 0.9)
        self.assertEqual(r["accept_item"], 3)

    def test_product_ranks_variants(self):
        body = {
            "variants": [
                v("low", "text/plain", "en"),   # 0.5 * 1.0 = 0.5
                v("high", "application/json", "en"),  # 0.9 * 0.8 = 0.72
            ],
            "accept": "text/plain;q=0.5, application/json;q=0.9",
            "accept_language": "en;q=0.8, *;q=0.1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "high")
        self.assertAlmostEqual(r["accept_q"], 0.9)
        self.assertAlmostEqual(r["accept_language_q"], 0.8)
        self.assertEqual(r["accept_item"], 2)
        self.assertEqual(r["accept_language_item"], 1)

    def test_language_prefix_on_subtag_boundary(self):
        body = {
            "variants": [v("a", "text/html", "zh-Hans-CN")],
            "accept_language": "zh-Hans;q=0.7, *;q=0.1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "a")
        self.assertAlmostEqual(r["accept_language_q"], 0.7)
        self.assertEqual(r["accept_language_item"], 1)

    def test_language_prefix_must_end_at_boundary(self):
        # "en" must NOT prefix-match "english".
        body = {
            "variants": [v("a", "text/html", "english")],
            "accept_language": "en;q=0.9, *;q=0.1",
        }
        r = negotiate(body)
        self.assertAlmostEqual(r["accept_language_q"], 0.1)
        self.assertEqual(r["accept_language_item"], 2)

    def test_exact_media_q0_is_not_overridden_by_wildcard(self):
        body = {
            "variants": [v("a", "text/html", "en"), v("b", "text/plain", "en")],
            "accept": "text/html;q=0, */*;q=1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "b")

    def test_concrete_language_q0_is_not_overridden_by_star(self):
        body = {
            "variants": [v("a", "text/html", "en"), v("b", "text/html", "fr")],
            "accept_language": "en;q=0, *;q=1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "b")

    def test_q0_wildcard_rejects_all(self):
        body = {
            "variants": [v("a", "text/html", "en")],
            "accept": "*/*;q=0",
            "accept_language": "*;q=0",
        }
        with self.assertRaises(NoViableVariantError):
            negotiate(body)

    def test_tie_break_media_specificity_then_language_then_order(self):
        # Both products are 0.5; first variant has exact media (3 vs 1).
        body = {
            "variants": [
                v("exact", "text/html", "en"),
                v("wild", "application/json", "en"),
            ],
            "accept": "text/html;q=0.5, application/json;q=0.5, */*;q=0.5",
            "accept_language": "en;q=1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "exact")

        # Equal product and equal media specificity -> language specificity
        # decides: en (1 subtag) loses to en-US (2 subtags).
        body = {
            "variants": [
                v("en", "text/html", "en"),
                v("enus", "text/html", "en-US"),
            ],
            "accept": "text/html;q=0.5",
            "accept_language": "en-US;q=1, en;q=1, *;q=0.5",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "enus")

        # Fully tied -> input order.
        body = {
            "variants": [v("first", "text/html", "en"), v("second", "text/html", "en")],
            "accept": "text/html",
            "accept_language": "en",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "first")

    def test_equal_product_exact_decimal_comparison(self):
        # 0.5*0.2 == 0.1*1.0 exactly as decimals; both axes specificity equal,
        # input order decides.
        body = {
            "variants": [
                v("a", "text/html", "en"),
                v("b", "text/plain", "fr"),
            ],
            "accept": "text/html;q=0.5, text/plain;q=0.1",
            "accept_language": "en;q=0.2, fr;q=1",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "a")

    def test_case_insensitive_matching(self):
        body = {
            "variants": [v("a", "TEXT/HTML", "EN-us")],
            "accept": "text/Html;q=0.3",
            "accept_language": "en-US;q=0.6",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "a")
        self.assertAlmostEqual(r["accept_q"], 0.3)
        self.assertAlmostEqual(r["accept_language_q"], 0.6)

    def test_whitespace_around_commas_and_semicolons_allowed(self):
        body = {
            "variants": [v("a", "text/html", "en")],
            "accept": "  text/html ; q=0.7 ,  */* ; q=0.1 ",
        }
        r = negotiate(body)
        self.assertAlmostEqual(r["accept_q"], 0.7)
        self.assertEqual(r["accept_item"], 1)

    def test_same_specificity_uses_earlier_header_item(self):
        body = {
            "variants": [v("a", "text/html", "en")],
            "accept": "text/html;q=0.4, text/html;q=0.9",
        }
        r = negotiate(body)
        self.assertAlmostEqual(r["accept_q"], 0.4)
        self.assertEqual(r["accept_item"], 1)

    def test_default_q_is_one(self):
        body = {
            "variants": [v("a", "text/html", "en-US")],
            "accept": "text/html",
            "accept_language": "en-US",
        }
        r = negotiate(body)
        self.assertEqual(r["accept_q"], 1.0)
        self.assertEqual(r["accept_language_q"], 1.0)

    def test_one_axis_default_other_explicit(self):
        body = {
            "variants": [v("a", "text/html", "fr"), v("b", "text/html", "en")],
            "accept_language": "en",
        }
        r = negotiate(body)
        self.assertEqual(r["id"], "b")
        self.assertIsNone(r["accept_item"])
        self.assertEqual(r["accept_language_item"], 1)

    def test_explicit_null_means_default(self):
        body = {"variants": [v("a", "text/html", "en")], "accept": None, "accept_language": None}
        r = negotiate(body)
        self.assertIsNone(r["accept_item"])
        self.assertIsNone(r["accept_language_item"])

    def test_item_numbers_refer_to_headers_independently(self):
        body = {
            "variants": [v("a", "application/json", "fr-FR")],
            "accept": "text/html;q=0.1, application/json;q=0.8",
            "accept_language": "en;q=0.1, fr;q=0.7, fr-FR;q=0.9",
        }
        r = negotiate(body)
        self.assertEqual(r["accept_item"], 2)
        self.assertEqual(r["accept_language_item"], 3)

    # ---- 422 validation ------------------------------------------------------

    def _expect_422(self, body, loc_part=None):
        with self.assertRaises(NegotiationError) as cm:
            negotiate(body)
        if loc_part is not None:
            loc = cm.exception.loc
            for part in loc_part:
                self.assertIn(part, loc)
        return cm.exception

    def test_not_an_object(self):
        self._expect_422([1], ["body"])

    def test_variants_must_be_list(self):
        self._expect_422({"variants": 3}, ["variants"])

    def test_zero_or_too_many_variants(self):
        self._expect_422({"variants": []}, ["variants"])
        self._expect_422(
            {"variants": [v(f"id{i}", "text/html", "en") for i in range(31)]}, ["variants"]
        )

    def test_bad_variant_fields_report_index(self):
        self._expect_422({"variants": [{"id": "", "media_type": "text/html", "language": "en"}]},
                         ["variant", 0, "id"])
        self._expect_422({"variants": [{"id": "a", "media_type": "text/*", "language": "en"}]},
                         ["variant", 0, "media_type"])
        self._expect_422({"variants": [{"id": "a", "media_type": "*/*", "language": "en"}]},
                         ["variant", 0, "media_type"])
        self._expect_422({"variants": [{"id": "a", "media_type": "text/html", "language": "*"}]},
                         ["variant", 0, "language"])
        self._expect_422({"variants": [{"id": "a", "media_type": "text/html;q=1", "language": "en"}]},
                         ["variant", 0, "media_type"])
        self._expect_422({"variants": [{"id": "a", "media_type": "html", "language": "en"}]},
                         ["variant", 0, "media_type"])
        self._expect_422({"variants": [{"id": "a", "media_type": "*/text", "language": "en"}]},
                         ["variant", 0, "media_type"])

    def test_duplicate_variant_id(self):
        body = {"variants": [v("x", "text/html", "en"), v("x", "text/plain", "fr")]}
        exc = self._expect_422(body, ["variant", 1, "id"])
        self.assertIn("duplicate", exc.msg)

    def test_empty_header_items(self):
        body = {"variants": [v("a", "text/html", "en")], "accept": "text/html,,*/*"}
        self._expect_422(body, ["header", "accept", 2])
        body = {"variants": [v("a", "text/html", "en")], "accept": ""}
        self._expect_422(body, ["header", "accept", 1])
        body = {"variants": [v("a", "text/html", "en")], "accept": "  "}
        self._expect_422(body, ["header", "accept", 1])

    def test_too_many_header_items(self):
        value = ", ".join(["text/html"] * 17)
        body = {"variants": [v("a", "text/html", "en")], "accept": value}
        exc = self._expect_422(body, ["header", "accept", 17])
        self.assertIn("16", exc.msg)

    def test_bad_q(self):
        for bad in ["1.0001", "1.1", "-0.1", ".5", "0.", "2", "abc"]:
            body = {"variants": [v("a", "text/html", "en")], "accept": f"text/html;q={bad}"}
            self._expect_422(body, ["header", "accept", 1])

    def test_q_zero_and_one_valid(self):
        body = {"variants": [v("a", "text/html", "en"), v("b", "text/plain", "en")],
                "accept": "text/html;q=0, text/plain;q=1.000"}
        r = negotiate(body)
        self.assertEqual(r["id"], "b")

    def test_unsupported_or_duplicate_parameters(self):
        body = {"variants": [v("a", "text/html", "en")],
                "accept": "text/html;q=0.5;level=1"}
        self._expect_422(body, ["header", "accept", 1])
        body = {"variants": [v("a", "text/html", "en")],
                "accept": "text/html;q=0.5;q=0.2"}
        self._expect_422(body, ["header", "accept", 1])
        body = {"variants": [v("a", "text/html", "en")],
                "accept_language": "en;q=0.5;level=1"}
        self._expect_422(body, ["header", "accept_language", 1])

    def test_malformed_media_and_language_items(self):
        for bad in ["text", "text/html/extra", "text/", "/html", "te xt/html"]:
            body = {"variants": [v("a", "text/html", "en")], "accept": bad}
            self._expect_422(body, ["header", "accept", 1])
        for bad in ["en-US-", "en_US", "en-", "-en", "en; q "]:
            body = {"variants": [v("a", "text/html", "en")], "accept_language": bad}
            self._expect_422(body, ["header", "accept_language", 1])

    def test_header_must_be_string(self):
        body = {"variants": [v("a", "text/html", "en")], "accept": 5}
        self._expect_422(body, ["header", "accept"])

    def test_error_points_at_offending_item_index(self):
        body = {
            "variants": [v("a", "text/html", "en")],
            "accept": "text/html, garbage, */*",
        }
        exc = self._expect_422(body)
        self.assertEqual(exc.loc, ["header", "accept", 2])


if __name__ == "__main__":
    unittest.main()
