"""Nexus lifecycle contracts without network calls or private credentials."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / ".github/workflows/scripts/maven-staging.py"
spec = importlib.util.spec_from_file_location("maven_staging", SCRIPT)
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.record = Path(self.directory.name) / "context.json"
        self.context = {"profile": "abc123", "repository": "orgapachehugegraph-1160"}
        self.record.write_text(json.dumps(self.context))
        self.metadata = {"profileId": "abc123", "repositoryId": "orgapachehugegraph-1160", "type": "open", "transitioning": False}

    def test_http_parser_accepts_wrapped_profiles_and_unwrapped_metadata(self):
        settings = Path(self.directory.name) / "settings.xml"
        settings.write_text('<settings><servers><server><id>apache.releases.https</id><username>test</username><password>fixture</password></server></servers></settings>')
        nexus = staging.Nexus(settings)
        for payload, expected in (({"data": [{"id": "abc123"}]}, [{"id": "abc123"}]),
                                  (self.metadata, self.metadata)):
            response = Mock()
            response.read.return_value = json.dumps(payload).encode()
            manager = Mock()
            manager.__enter__ = Mock(return_value=response)
            manager.__exit__ = Mock(return_value=False)
            opener = Mock()
            opener.open.return_value = manager
            with patch.object(staging.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(nexus.request("/staging/profiles"), expected)

    def test_start_records_exact_repository_and_deploy_url(self):
        nexus = Mock()
        nexus.request.side_effect = [[{"id": "abc123", "name": "org.apache.hugegraph"}], {"stagedRepositoryId": "orgapachehugegraph-1160"}]
        environment = Path(self.directory.name) / "environment"
        with patch.dict(os.environ, {"GITHUB_ENV": str(environment)}):
            staging.start(nexus, self.record)
        self.assertEqual(json.loads(self.record.read_text()), self.context)
        self.assertEqual(environment.read_text(), "MAVEN_DEPLOY_URL=https://repository.apache.org/service/local/staging/deployByRepositoryId/orgapachehugegraph-1160\n")
        self.assertEqual(nexus.request.call_args_list[1].args[0], "/staging/profiles/abc123/start")

    def test_close_finishes_only_recorded_repository_and_waits(self):
        nexus = Mock()
        closed = dict(self.metadata, type="closed")
        nexus.request.side_effect = [self.metadata, None, closed]
        staging.close(nexus, self.record)
        self.assertEqual(nexus.request.call_args_list[1].args, ("/staging/profiles/abc123/finish", {"stagedRepositoryId": "orgapachehugegraph-1160", "description": "Close HugeGraph candidate after successful upload"}))
        self.assertEqual(json.loads(self.record.read_text())["state"], "closed")
        self.assertIn("staging_state=closed", (self.record.parent / "release-context.txt").read_text())

    def test_mismatched_repository_refuses_finish(self):
        nexus = Mock()
        nexus.request.return_value = dict(self.metadata, repositoryId="orgapachehugegraph-1161")
        with self.assertRaisesRegex(SystemExit, "does not match"):
            staging.close(nexus, self.record)
        self.assertEqual(nexus.request.call_count, 1)

    def test_failed_close_reports_failure_without_closed_receipt(self):
        nexus = Mock()
        nexus.request.side_effect = [self.metadata, None, self.metadata]
        with patch.object(staging.time, "monotonic", side_effect=[0, 0, 0, 0, 0, 301]):
            with self.assertRaisesRegex(SystemExit, "did not complete"):
                staging.close(nexus, self.record)
        self.assertNotIn("state", json.loads(self.record.read_text()))

    def test_close_waits_for_open_transition_before_finish(self):
        nexus = Mock()
        nexus.request.side_effect = [dict(self.metadata, transitioning=True), self.metadata,
                                     None, dict(self.metadata, type="closed")]
        with patch.object(staging.time, "sleep") as sleep:
            staging.close(nexus, self.record)
        sleep.assert_called_once_with(15)
        self.assertEqual(nexus.request.call_args_list[2].args[0], "/staging/profiles/abc123/finish")

    def test_transition_timeout_refuses_finish(self):
        nexus = Mock()
        nexus.request.return_value = dict(self.metadata, transitioning=True)
        with patch.object(staging.time, "monotonic", side_effect=[0, 301]):
            with self.assertRaisesRegex(SystemExit, "did not complete"):
                staging.close(nexus, self.record)
        self.assertEqual(nexus.request.call_count, 0)

    def test_closed_response_after_deadline_is_rejected(self):
        nexus = Mock()
        nexus.request.return_value = dict(self.metadata, type="closed")
        with patch.object(staging.time, "monotonic", side_effect=[0, 299, 301]):
            with self.assertRaisesRegex(SystemExit, "did not complete"):
                staging.close(nexus, self.record)
        self.assertEqual(nexus.request.call_args.kwargs, {"timeout": 1})
        self.assertNotIn("state", json.loads(self.record.read_text()))

    def test_already_closed_is_read_only(self):
        nexus = Mock()
        nexus.request.return_value = dict(self.metadata, type="closed")
        staging.close(nexus, self.record)
        self.assertEqual(nexus.request.call_count, 1)

    def test_credentials_resolve_environment_without_executing_settings(self):
        settings = Path(self.directory.name) / "settings.xml"
        settings.write_text('<settings><servers><server><id>apache.releases.https</id><username>${env.TEST_USER}</username><password>${env.TEST_PASSWORD}</password></server></servers></settings>')
        with patch.dict(os.environ, {"TEST_USER": "test", "TEST_PASSWORD": "fixture"}):
            self.assertEqual(staging.credentials(settings), ("test", "fixture"))
        with patch.dict(os.environ, {"TEST_USER": "test", "TEST_PASSWORD": "{encrypted}"}):
            with self.assertRaisesRegex(SystemExit, "resolved plain values"):
                staging.credentials(settings)


if __name__ == "__main__":
    unittest.main()
