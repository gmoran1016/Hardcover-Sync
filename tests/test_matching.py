import unittest

from matching import candidate_title, choose_match, normalise, result_score


class MatchingTests(unittest.TestCase):
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
