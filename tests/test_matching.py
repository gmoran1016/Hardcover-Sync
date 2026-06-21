import unittest

from matching import choose_match, normalise, result_score


class MatchingTests(unittest.TestCase):
    def test_normalise_handles_unicode_punctuation_and_parentheses(self):
        self.assertEqual(normalise("Café—World (Book One)!"), "cafe world")

    def test_choose_match_uses_author_and_rejects_ambiguity(self):
        candidates = [
            ("The Stand by Stephen King", "correct"),
            ("The Stand by Roberto Bolaño", "wrong"),
        ]
        self.assertEqual(choose_match("The Stand", "Stephen King", candidates), "correct")

    def test_choose_match_does_not_fall_back_to_first_result(self):
        candidates = [("A Completely Different Book", "wrong")]
        self.assertIsNone(choose_match("Dune", "Frank Herbert", candidates))

    def test_author_mismatch_reduces_score(self):
        good = result_score("Dune", "Frank Herbert", "Dune — Frank Herbert")
        bad = result_score("Dune", "Frank Herbert", "Dune — Jane Austen")
        self.assertGreater(good, bad)


if __name__ == "__main__":
    unittest.main()
