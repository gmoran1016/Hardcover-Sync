import unittest

from main import _parse_args


class CliTests(unittest.TestCase):
    def test_default_mode_runs_scheduler(self):
        args = _parse_args([])
        self.assertFalse(args.once)
        self.assertFalse(args.diagnose_auth)

    def test_once_mode(self):
        args = _parse_args(["--once"])
        self.assertTrue(args.once)

    def test_auth_diagnostics_mode(self):
        args = _parse_args(["--diagnose-auth"])
        self.assertTrue(args.diagnose_auth)

    def test_modes_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            _parse_args(["--once", "--diagnose-auth"])


if __name__ == "__main__":
    unittest.main()
