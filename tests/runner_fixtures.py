"""Non-discovered fixtures used to verify the repository test runner."""
import unittest


class PassingTest(unittest.TestCase):
    def test_passes(self) -> None:
        self.assertTrue(True)


class FailingTest(unittest.TestCase):
    def test_fails(self) -> None:
        self.fail("intentional runner fixture failure")
