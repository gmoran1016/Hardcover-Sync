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


if __name__ == "__main__":
    unittest.main()
