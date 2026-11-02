"""Tests for scripts/monitor.py's alert-issue state machine (gh faked). python -m unittest discover -s scripts"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import monitor  # noqa: E402


class FakeGh:
    def __init__(self, open_issue: str | None) -> None:
        self.open_issue = open_issue
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return self.open_issue or ""
        if args[:2] == ("issue", "create"):
            return "https://github.com/x/y/issues/9"
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls if c[:2] != ("issue", "list")]


class SyncIssueTest(unittest.TestCase):
    def run_sync(self, open_issue: str | None, failures: list[str], warnings: list[str]) -> FakeGh:
        fake = FakeGh(open_issue)
        orig = monitor.gh
        monitor.gh = fake
        try:
            monitor.sync_issue(failures, warnings, ["ok"], "https://run")
        finally:
            monitor.gh = orig
        return fake

    def test_first_failure_opens_one_issue(self) -> None:
        self.assertEqual(
            self.run_sync(None, ["en down"], []).verbs(), ["label create", "issue create"]
        )

    def test_persisting_failure_edits_in_place_without_commenting(self) -> None:
        verbs = self.run_sync("7", ["en down"], []).verbs()
        self.assertEqual(verbs, ["issue edit"])

    def test_runway_warning_alone_opens_issue(self) -> None:
        self.assertIn("issue create", self.run_sync(None, [], ["runway low"]).verbs())

    def test_recovery_comments_once_and_closes(self) -> None:
        self.assertEqual(self.run_sync("7", [], []).verbs(), ["issue comment", "issue close"])

    def test_healthy_with_no_issue_does_nothing(self) -> None:
        self.assertEqual(self.run_sync(None, [], []).verbs(), [])


if __name__ == "__main__":
    unittest.main()
