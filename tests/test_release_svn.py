"""Real local SVN migration checks; no publisher credentials or uploads."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/workflows/scripts/release-svn.sh"


class ReleaseSvnTest(unittest.TestCase):
    def command(self, *args, success=True):
        result = subprocess.run(args, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result.stdout

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        root = Path(self.storage.name)
        self.env = dict(os.environ)
        self.command("svnadmin", "create", str(root / "svn"))
        self.url = (root / "svn").as_uri()
        self.env["SVN_TEST_ROOT"] = self.url
        keys = root / "KEYS"
        keys.write_text("Public keys\n")
        self.command("svnmucc", "-m", "Fixture",
                     "mkdir", self.url + "/dev", "mkdir", self.url + "/dev/hugegraph",
                     "mkdir", self.url + "/dev/hugegraph/1.8.0",
                     "mkdir", self.url + "/dev/hugegraph/1.8.0/RC1",
                     "put", str(keys), self.url + "/dev/hugegraph/KEYS",
                     "mkdir", self.url + "/release", "mkdir", self.url + "/release/hugegraph",
                     "mkdir", self.url + "/release/hugegraph/1.7.0",
                     "put", str(keys), self.url + "/release/hugegraph/KEYS")

    def promote(self, path="1.8.0/RC1", keys="false", delete="false", previous="", success=True):
        return self.command("bash", str(SCRIPT), "promote", "1.8.0", path,
                            keys, delete, previous, success=success)

    def revision(self):
        return self.command("svn", "info", "--show-item", "revision", self.url).strip()

    def test_atomic_migration_keys_and_cleanup(self):
        revision = int(self.revision())
        self.promote(keys="true", delete="true", previous="1.7.0")
        self.assertEqual(int(self.revision()), revision + 1)
        self.command("svn", "info", self.url + "/release/hugegraph/1.8.0")
        self.command("svn", "info", self.url + "/dev/hugegraph/1.8.0/RC1", success=False)
        self.command("svn", "info", self.url + "/release/hugegraph/1.7.0", success=False)
        self.assertEqual(self.command("svn", "cat", self.url + "/dev/hugegraph/KEYS"),
                         self.command("svn", "cat", self.url + "/release/hugegraph/KEYS"))

    def test_invalid_path_or_cleanup_changes_nothing(self):
        for path, previous in (("../1.8.0", "1.7.0"), ("1.8.0/RC0", "1.7.0"),
                               ("1.8.0/RC1", "1.8.0"), ("1.8.0/RC1", "../1.7.0"),
                               ("1.8.0/RC1", "9.9.9")):
            with self.subTest(path=path, previous=previous):
                revision = self.revision()
                self.promote(path, "true", "true", previous, success=False)
                self.assertEqual(self.revision(), revision)

    def test_existing_release_target_changes_nothing(self):
        self.command("svnmucc", "-m", "Existing release", "mkdir", self.url + "/release/hugegraph/1.8.0")
        revision = self.revision()
        self.promote(success=False)
        self.assertEqual(self.revision(), revision)
