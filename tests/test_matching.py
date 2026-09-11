import unittest

import matching
from matching import candidate_title, choose_match, normalise, result_score


class MatchingTests(unittest.TestCase):
    def test_sole_sequel_is_not_accepted_for_shorter_title(self):
        self.assertIsNone(
            choose_match(
                "Dune", "Frank Herbert", [("Dune Messiah\nFrank Herbert", "wrong")]
            )
        )

    def test_sole_explicit_wrong_author_is_rejected(self):
        for evidence in (
            "Dune\nJane Austen",
            "Dune by Jane Austen",
            "Dune — Jane Austen",
        ):
            with self.subTest(evidence=evidence):
                self.assertIsNone(
                    choose_match("Dune", "Frank Herbert", [(evidence, "wrong")])
                )

    def test_author_name_substrings_are_not_author_agreement(self):
        self.assertIsNone(
            choose_match("Dune", "Frank Herbert", [("Dune\nFrank Herbertson", "wrong")])
        )

    def test_canonical_provider_identity_ignores_goodreads_slug_and_tracking(self):
        canonical = getattr(matching, "canonical_book_url", lambda *_: None)
        self.assertEqual(
            canonical(
                "goodreads",
                "https://goodreads.com/book/show/44767458.Dune?from_search=true#reviews",
            ),
            "https://www.goodreads.com/book/show/44767458",
        )
        self.assertEqual(
            canonical(
                "storygraph", "https://app.thestorygraph.com/books/abc-123/?ref=search"
            ),
            "https://app.thestorygraph.com/books/abc-123",
        )

    def test_canonical_provider_identity_rejects_unsafe_or_nonbook_urls(self):
        canonical = getattr(matching, "canonical_book_url", lambda *_: "unsafe")
        for destination, url in (
            ("goodreads", "http://www.goodreads.com/book/show/1"),
            ("goodreads", "https://www.goodreads.com.evil.test/book/show/1"),
            ("goodreads", "https://user@www.goodreads.com/book/show/1"),
            ("goodreads", "https://www.goodreads.com/book/show/wrong"),
            ("goodreads", "https://www.goodreads.com:9443/book/show/1"),
            ("goodreads", "https://www.goodreads.com/book/show/1/editions"),
            ("goodreads", "https://www.goodreads.com/book/show/1%2F..%2F2"),
            ("storygraph", "https://evil.test/books/abc-123"),
            ("storygraph", "https://app.thestorygraph.com/books/new"),
            ("storygraph", "https://app.thestorygraph.com/books/abc/editions"),
            ("storygraph", "https://app.thestorygraph.com/books/../users/sign_in"),
            ("other", "https://www.goodreads.com/book/show/1"),
        ):
            with self.subTest(destination=destination, url=url):
                self.assertIsNone(canonical(destination, url))

    def test_normalise_handles_unicode_punctuation_and_parentheses(self):
        self.assertEqual(normalise("Café—World (Book One)!"), "cafe world")

    def test_choose_match_uses_author_and_rejects_ambiguity(self):
        candidates = [
            ("The Stand by Stephen King", "correct"),
            ("The Stand by Roberto Bolaño", "wrong"),
        ]
        self.assertEqual(
            choose_match("The Stand", "Stephen King", candidates), "correct"
        )

    def test_choose_match_does_not_fall_back_to_first_result(self):
        candidates = [("A Completely Different Book", "wrong")]
        self.assertIsNone(choose_match("Dune", "Frank Herbert", candidates))

    def test_author_mismatch_reduces_score(self):
        good = result_score("Dune", "Frank Herbert", "Dune — Frank Herbert")
        bad = result_score("Dune", "Frank Herbert", "Dune — Jane Austen")
        self.assertGreater(good, bad)

    def test_candidate_title_uses_first_nonempty_line(self):
        self.assertEqual(
            candidate_title(
                "\nCrossroads of Twilight (The Wheel of Time, #10)\nby Robert Jordan"
            ),
            "Crossroads of Twilight (The Wheel of Time, #10)",
        )

    def test_choose_match_handles_storygraph_series_before_title(self):
        candidates = [
            (
                "The Wheel of Time #11\nKnife of Dreams\nRobert Jordan",
                "https://app.thestorygraph.com/books/knife-of-dreams",
            )
        ]
        self.assertEqual(
            choose_match("Knife of Dreams", "Robert Jordan", candidates),
            "https://app.thestorygraph.com/books/knife-of-dreams",
        )

    def test_choose_match_collapses_equivalent_result_duplicates(self):
        candidates = [
            (
                "The Wheel of Time #11\nKnife of Dreams\nRobert Jordan",
                "https://app.thestorygraph.com/books/first",
            ),
            (
                "The Wheel Of Time #11\nKnife of Dreams\nRobert Jordan",
                "https://app.thestorygraph.com/books/second",
            ),
            (
                "Knife of Dreams: Book Eleven 11 of The Wheel of Time\nRobert Jordan",
                "https://app.thestorygraph.com/books/third",
            ),
        ]
        self.assertEqual(
            choose_match("Knife of Dreams", "Robert Jordan", candidates),
            "https://app.thestorygraph.com/books/first",
        )

    def test_choose_match_rejects_different_near_ties(self):
        candidates = [
            ("Dune\nFrank Herbert", "https://app.thestorygraph.com/books/first"),
            ("Dune 1\nFrank Herbert", "https://app.thestorygraph.com/books/second"),
        ]
        self.assertIsNone(choose_match("Dune", "Frank Herbert", candidates))

    def test_choose_match_selects_canonical_goodreads_work(self):
        candidates = [
            (
                "The Crossroads of Twilight by Robert Jordan Unabridged CD "
                "Audiobook (The Wheel of Time Series)\n"
                "by Robert Jordan, Kate Reading & Michael Kramer (Narrator)",
                "https://www.goodreads.com/book/show/198612238",
            ),
            (
                "Crossroads of Twilight by Jordan, Robert. "
                "(Tor Fantasy,2003) [Hardcover]\nby Robert Jordan",
                "https://www.goodreads.com/book/show/161987089",
            ),
            (
                "Crossroads Of Twilight - Robert Jordan\n"
                "by Robert Jordan, Unknown Author",
                "https://www.goodreads.com/book/show/246486965",
            ),
            (
                "Crossroads of Twilight (The Wheel of Time, #10)\nby Robert Jordan",
                "https://www.goodreads.com/book/show/113435.Crossroads_of_Twilight",
            ),
            (
                "Glimmers: Prologue to Crossroads of Twilight "
                "(Wheel of Time, #9.9)\nby Robert Jordan",
                "https://www.goodreads.com/book/show/4968078-glimmers",
            ),
        ]
        self.assertEqual(
            choose_match("Crossroads of Twilight", "Robert Jordan", candidates),
            "https://www.goodreads.com/book/show/113435.Crossroads_of_Twilight",
        )


if __name__ == "__main__":
    unittest.main()
