import unittest
from unittest.mock import patch

import container_entrypoint


class ContainerEntrypointTests(unittest.TestCase):
    def test_log_flushes_to_stdout(self):
        with patch("builtins.print") as output:
            container_entrypoint.log("hello")
        output.assert_called_once_with(
            "[hardcover-sync-entrypoint] hello",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()
