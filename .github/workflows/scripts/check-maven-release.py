#!/usr/bin/env python3
"""Check source version, or effective release POM and private settings server id.

Usage: check-maven-release.py VERSION POM [SETTINGS]
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

version, pom_path, *settings_paths = sys.argv[1:]
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("Use a numeric release version, without SNAPSHOT or RC suffix")
ns = {"m": "http://maven.apache.org/POM/4.0.0"}
model = ET.parse(pom_path).getroot()
projects = [model] if model.tag.split("}")[-1] == "project" else model.findall("m:project", ns)
if not projects:
    raise SystemExit("POM contains no projects")

server_id = "apache.releases.https"
url = "https://repository.apache.org/service/local/staging/deploy/maven2"
if settings_paths:
    settings = ET.parse(settings_paths[0]).getroot()
    server_ids = {entry.text for server in settings.iter()
                  if server.tag.split("}")[-1] == "server"
                  for entry in server if entry.tag.split("}")[-1] == "id"}
    if server_id not in server_ids:
        raise SystemExit(f"M2_SETTINGS lacks server id {server_id}")

for project in projects:
    artifact = project.findtext("m:artifactId", namespaces=ns)
    actual = project.findtext("m:version", namespaces=ns)
    if actual == "${revision}":
        actual = project.findtext("m:properties/m:revision", namespaces=ns)
    if actual != version:
        raise SystemExit(f"Source version mismatch for {artifact}: {actual!r} != {version!r}")
    if settings_paths:
        repository = project.find("m:distributionManagement/m:repository", ns)
        if repository is None or repository.findtext("m:id", namespaces=ns) != server_id or repository.findtext("m:url", namespaces=ns) != url:
            raise SystemExit(f"Expected Apache Maven staging upload target for {artifact}")

if not settings_paths and os.environ.get("COMPONENT") == "toolchain":
    if model.findtext("m:properties/m:hugegraph.version", namespaces=ns) != "1.8.0":
        raise SystemExit("Toolchain must compile against SDK 1.8.0")
    dist = ET.parse(Path(pom_path).parent / "hugegraph-dist/pom.xml").getroot()
    if dist.findtext("m:artifactId", namespaces=ns) != "hugegraph-toolchain-dist":
        raise SystemExit("Toolchain dist artifactId must be hugegraph-toolchain-dist")

print(f"Checked {len(projects)} Maven project(s), version {version}" + (f", upload target {url} ({server_id})" if settings_paths else ""))
