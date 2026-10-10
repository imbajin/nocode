"""Check that publisher deploy settings actually flush artifacts with build extensions."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MavenDeployTest(unittest.TestCase):
    def test_publisher_configuration_deploys_extension_reactor(self):
        maven = shutil.which("mvn")
        self.assertIsNotNone(maven, "Maven is required for the file repository regression")
        workflows = ("release-server.yml", "release_toolchain.yml", "release_computer.yml")
        options = set()
        for workflow in workflows:
            text = (ROOT / ".github/workflows" / workflow).read_text()
            match = re.search(r"-DdeployAtEnd=(true|false)", text)
            options.add(match.group(0) if match else "-DdeployAtEnd=false")
        self.assertEqual(len(options), 1, "Publishers must use the same tested deployment behavior")
        with tempfile.TemporaryDirectory(prefix="nc-maven-", dir="/tmp") as temporary:
            root = Path(temporary)
            settings = root / "settings.xml"
            settings.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"/>')
            repository = root / "repository"
            (root / "pom.xml").write_text(f'''<project xmlns="http://maven.apache.org/POM/4.0.0">
              <modelVersion>4.0.0</modelVersion><groupId>example.nocode</groupId>
              <artifactId>publisher-fixture</artifactId><version>1.0.0</version><packaging>pom</packaging>
              <modules><module>ordinary</module><module>extension</module></modules>
              <distributionManagement><repository><id>file-test</id><url>{repository.as_uri()}</url></repository></distributionManagement>
              <build><plugins><plugin><groupId>org.apache.maven.plugins</groupId>
              <artifactId>maven-deploy-plugin</artifactId><version>2.8.2</version></plugin></plugins></build>
            </project>''')
            for name in ("ordinary", "extension"):
                module = root / name
                module.mkdir()
                extension = '''<build><extensions><extension><groupId>kr.motd.maven</groupId>
                  <artifactId>os-maven-plugin</artifactId><version>1.7.1</version>
                  </extension></extensions></build>''' if name == "extension" else ""
                (module / "pom.xml").write_text(f'''<project xmlns="http://maven.apache.org/POM/4.0.0">
                  <modelVersion>4.0.0</modelVersion><parent><groupId>example.nocode</groupId>
                  <artifactId>publisher-fixture</artifactId><version>1.0.0</version></parent>
                  <artifactId>{name}</artifactId><packaging>pom</packaging>{extension}</project>''')
            # Also exercise the exact-target override used for optional close.
            dedicated = root / "dedicated"
            for destination, override in (
                (repository, []),
                (dedicated, [f"-DaltDeploymentRepository=file-test::default::{dedicated.as_uri()}"]),
            ):
                result = subprocess.run([maven, "-B", "-ntp", "-s", str(settings), "-gs", str(settings),
                                         f"-Dmaven.repo.local={root / 'm2'}", "deploy", next(iter(options)), *override],
                                        cwd=root, env=os.environ, capture_output=True, text=True, timeout=300)
                self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-2000:])
                # Exit status alone is insufficient: old deferred deploy can silently publish nothing.
                for artifact in ("publisher-fixture", "ordinary", "extension"):
                    with self.subTest(artifact=artifact):
                        pom = destination / "example/nocode" / artifact / "1.0.0" / f"{artifact}-1.0.0.pom"
                        self.assertTrue(pom.is_file(), f"Missing real deployed POM: {pom}\n{result.stdout[-6000:]}")


if __name__ == "__main__":
    unittest.main()
