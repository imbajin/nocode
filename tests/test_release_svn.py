"""Exercise candidate signatures and SVN transactions without publisher credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / ".github/workflows/scripts/release-svn.sh"
VERSION = "1.8.0"


class ReleaseSvnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("git", "gpg", "svn", "svnadmin", "svnmucc", "shasum"):
            if not shutil.which(tool):
                raise RuntimeError(f"Required test tool missing: {tool}")
        # Keep gpg-agent's Unix socket path below macOS's socket length limit.
        cls.storage = tempfile.TemporaryDirectory(prefix="nc-release-", dir="/tmp")
        cls.root = Path(cls.storage.name)
        cls.gpg_home = cls.root / "gnupg"
        cls.gpg_home.mkdir(mode=0o700)
        cls.env = {**os.environ, "GNUPGHOME": str(cls.gpg_home)}
        for name in ("SVN_USERNAME", "SVN_PASSWORD", "SVN_TEST_ROOT", "GPG_KEY_ID"):
            cls.env.pop(name, None)
        cls.run_cmd("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                    "--quick-generate-key", "Release test <release@example.invalid>", "rsa2048", "sign", "0")
        keys = cls.run_cmd("gpg", "--with-colons", "--list-secret-keys").stdout
        cls.fingerprint = next(line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:"))
        cls.env["GPG_KEY_ID"] = cls.fingerprint
        cls.source = cls.root / "source"
        cls.source.mkdir()
        (cls.source / "LICENSE").write_text("Test license\n")
        (cls.source / "NOTICE").write_text("Test notice\n")
        (cls.source / "README.md").write_text("Source provenance fixture\n")
        cls.run_cmd("git", "init", str(cls.source))
        cls.run_cmd("git", "-C", str(cls.source), "add", ".")
        cls.run_cmd("git", "-C", str(cls.source), "-c", "user.name=Release test",
                    "-c", "user.email=release@example.invalid", "commit", "-m", "Fixture")
        cls.sha = cls.run_cmd("git", "-C", str(cls.source), "rev-parse", "HEAD").stdout.strip()
        target = cls.source / "target"
        target.mkdir()
        for project in ("hugegraph", "hugegraph-toolchain"):
            with tarfile.open(target / f"apache-{project}-{VERSION}.tar.gz", "w:gz") as archive:
                archive.add(cls.source / "NOTICE", arcname=f"apache-{project}-{VERSION}/NOTICE")
        cls.packages = {}
        for component in ("server", "toolchain", "computer", "ai"):
            destination = cls.root / f"packages-{component}"
            result = cls.run_cmd("bash", str(SCRIPT), "prepare", component, VERSION,
                                 f"{VERSION}/RC1", str(cls.source), str(destination))
            if cls.sha not in result.stdout:
                raise AssertionError("Preparation did not record the source SHA")
            cls.packages[component] = destination

    @classmethod
    def tearDownClass(cls):
        if shutil.which("gpgconf"):
            subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=cls.env, capture_output=True)
        cls.storage.cleanup()

    @classmethod
    def run_cmd(cls, *args, env=None, success=True):
        result = subprocess.run(args, env=env or cls.env, capture_output=True, text=True)
        if success and result.returncode:
            raise AssertionError(f"{args}:\n{result.stdout}\n{result.stderr}")
        if not success and not result.returncode:
            raise AssertionError(f"Unexpected success: {args}")
        return result

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(dir=self.root))
        repository = self.workspace / "svn"
        self.run_cmd("svnadmin", "create", str(repository))
        self.url = repository.as_uri()
        self.svn_env = {**self.env, "SVN_TEST_ROOT": self.url}
        new_keys = self.workspace / "new-keys"
        old_keys = self.workspace / "old-keys"
        new_keys.write_text("New public keys\n")
        old_keys.write_text("Old public keys\n")
        self.run_cmd("svnmucc", "-m", "Fixture", "mkdir", self.url + "/dev",
                     "mkdir", self.url + "/dev/hugegraph", "mkdir", self.url + "/release",
                     "mkdir", self.url + "/release/hugegraph",
                     "mkdir", self.url + "/release/hugegraph/1.7.0",
                     "put", str(new_keys), self.url + "/dev/hugegraph/KEYS",
                     "put", str(old_keys), self.url + "/release/hugegraph/KEYS")

    def script(self, *args, success=True, env=None):
        return self.run_cmd("bash", str(SCRIPT), *args, success=success, env=env or self.svn_env)

    def upload(self, component="server", path=f"{VERSION}/RC1", success=True, packages=None):
        return self.script("upload", VERSION, path, str(packages or self.packages[component]), success=success)

    def promote(self, keys="false", delete="false", previous="", success=True):
        return self.script("promote", VERSION, f"{VERSION}/RC1", keys, delete, previous, success=success)

    def revision(self):
        return self.run_cmd("svn", "info", "--show-item", "revision", self.url).stdout.strip()

    def test_prepare_components_and_source_provenance(self):
        for component, directory in self.packages.items():
            with self.subTest(component=component):
                archives = list(directory.glob("*.tar.gz"))
                self.assertEqual(len(archives), 2 if component in ("server", "toolchain") else 1)
                project = "hugegraph" if component == "server" else f"hugegraph-{component}"
                with tarfile.open(directory / f"apache-{project}-{VERSION}-src.tar.gz") as archive:
                    readme = archive.extractfile(f"apache-{project}-{VERSION}-src/README.md").read()
                    self.assertEqual(readme, b"Source provenance fixture\n")
                for package in archives:
                    self.assertTrue(Path(str(package) + ".asc").is_file())
                    self.assertTrue(Path(str(package) + ".sha512").is_file())

    def test_invalid_versions_and_paths(self):
        for version, path in (("../1.8.0", ""), ("1.8.0-SNAPSHOT", ""), (VERSION, "../x"),
                              (VERSION, "1.7.0/RC1"), (VERSION, f"{VERSION}/RC0"),
                              (VERSION, f"{VERSION}/RC1/../../x"), (VERSION, f"{VERSION}/RC1;id")):
            with self.subTest(version=version, path=path):
                self.script("validate-path", version, path, success=False)
        self.assertEqual(self.script("validate-path", VERSION, "").stdout.strip(), VERSION)

    def test_missing_binary_and_wrong_signer_fail(self):
        for version, env in (("1.8.1", self.env), (VERSION, {**self.env, "GPG_KEY_ID": "0000000000000000"})):
            destination = self.workspace / version
            self.script("prepare", "server", version, "", str(self.source), str(destination),
                        success=False, env=env)

    def test_dirty_source_rejected(self):
        checkout = self.workspace / "source"
        self.run_cmd("git", "clone", str(self.source), str(checkout))
        (checkout / "NOTICE").write_text("Changed tracked source\n")
        self.script("prepare", "ai", VERSION, "", str(checkout), str(self.workspace / "dirty"), success=False)

    def test_upload_reverifies_signature_hash_and_file_set(self):
        for corruption in ("archive", "signature", "checksum", "missing", "extra"):
            with self.subTest(corruption=corruption):
                directory = self.workspace / corruption
                shutil.copytree(self.packages["ai"], directory)
                package = next(directory.glob("*.tar.gz"))
                if corruption == "archive":
                    package.write_bytes(package.read_bytes() + b"corrupt")
                elif corruption == "signature":
                    Path(str(package) + ".asc").write_text("Invalid signature\n")
                elif corruption == "checksum":
                    Path(str(package) + ".sha512").write_text("0" * 128 + "  " + package.name + "\n")
                elif corruption == "missing":
                    Path(str(package) + ".asc").unlink()
                else:
                    (directory / "unexpected.txt").write_text("Not a release artifact\n")
                revision = self.revision()
                self.upload("ai", packages=directory, success=False)
                self.assertEqual(self.revision(), revision)

    def test_components_can_share_candidate_but_cannot_overwrite(self):
        self.upload("server")
        self.upload("toolchain")
        self.upload("computer")
        self.upload("ai")
        listing = self.run_cmd("svn", "list", self.url + f"/dev/hugegraph/{VERSION}/RC1").stdout
        self.assertEqual(len(listing.splitlines()), 18)
        revision = self.revision()
        self.upload("server", success=False)
        self.assertEqual(self.revision(), revision)

    def test_default_version_directory(self):
        self.upload("ai", path="")
        self.run_cmd("svn", "info", self.url + f"/dev/hugegraph/{VERSION}")

    def test_promote_keys_and_delete_are_one_transaction(self):
        self.upload()
        revision = int(self.revision())
        self.promote(keys="true", delete="true", previous="1.7.0")
        self.assertEqual(int(self.revision()), revision + 1)
        self.run_cmd("svn", "info", self.url + f"/release/hugegraph/{VERSION}")
        self.run_cmd("svn", "info", self.url + f"/dev/hugegraph/{VERSION}/RC1", success=False)
        self.run_cmd("svn", "info", self.url + "/release/hugegraph/1.7.0", success=False)
        dev_keys = self.run_cmd("svn", "cat", self.url + "/dev/hugegraph/KEYS").stdout
        release_keys = self.run_cmd("svn", "cat", self.url + "/release/hugegraph/KEYS").stdout
        self.assertEqual(dev_keys, "New public keys\n")
        self.assertEqual(dev_keys, release_keys)

    def test_failed_promote_keeps_candidate_and_release_unchanged(self):
        self.upload()
        for previous in (VERSION, "../1.7.0", "9.9.9"):
            revision = self.revision()
            self.promote(keys="true", delete="true", previous=previous, success=False)
            self.assertEqual(self.revision(), revision)
            self.run_cmd("svn", "info", self.url + f"/dev/hugegraph/{VERSION}/RC1")

    def test_existing_release_target_rejected(self):
        self.upload()
        self.run_cmd("svnmucc", "-m", "Existing release", "mkdir", self.url + f"/release/hugegraph/{VERSION}")
        revision = self.revision()
        self.promote(success=False)
        self.assertEqual(self.revision(), revision)

    def test_test_override_cannot_target_http(self):
        revision = self.revision()
        env = {**self.env, "SVN_TEST_ROOT": "https://example.invalid/dist"}
        self.script("upload", VERSION, "", str(self.packages["ai"]), env=env, success=False)
        self.assertEqual(self.revision(), revision)

    def test_upload_rejects_valid_signature_from_other_key(self):
        self.run_cmd("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                     "--quick-generate-key", "Other test <other@example.invalid>", "rsa2048", "sign", "0")
        keys = self.run_cmd("gpg", "--with-colons", "--list-secret-keys", "other@example.invalid").stdout
        other = next(line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:"))
        directory = self.workspace / "other-signer"
        self.script("prepare", "ai", VERSION, "", str(self.source), str(directory),
                    env={**self.env, "GPG_KEY_ID": other})
        revision = self.revision()
        self.upload("ai", packages=directory, success=False)
        self.assertEqual(self.revision(), revision)


if __name__ == "__main__":
    unittest.main()
