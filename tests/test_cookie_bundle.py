import unittest

from cookie_bundle import decode_cookie_bundle, encode_cookie_bundle


class CookieBundleTests(unittest.TestCase):
    def test_legacy_cookie_list_remains_supported(self):
        cookies = [{"name": "session", "value": "redacted"}]
        bundle = decode_cookie_bundle(cookies)
        self.assertEqual(bundle.cookies, cookies)
        self.assertIsNone(bundle.user_agent)

    def test_identity_bundle_round_trip(self):
        cookies = [{"name": "session", "value": "redacted"}]
        metadata = {"platform": "Windows", "mobile": False}
        encoded = encode_cookie_bundle(
            cookies,
            "Mozilla/5.0 Chrome/149",
            metadata,
        )
        bundle = decode_cookie_bundle(encoded)
        self.assertEqual(bundle.cookies, cookies)
        self.assertEqual(bundle.user_agent, "Mozilla/5.0 Chrome/149")
        self.assertEqual(bundle.user_agent_metadata, metadata)

    def test_invalid_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_cookie_bundle({"cookies": "not-a-list"})

    def test_non_object_cookie_record_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cookie records"):
            decode_cookie_bundle(["session=secret"])

    def test_decoded_cookies_do_not_alias_input(self):
        source = [{"name": "session", "value": "redacted", "sameSite": "Lax"}]
        bundle = decode_cookie_bundle(source)
        bundle.cookies[0].pop("sameSite")
        self.assertEqual(source[0]["sameSite"], "Lax")


if __name__ == "__main__":
    unittest.main()
