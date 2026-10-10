#!/usr/bin/env python3
"""Create and optionally close one dedicated Apache Maven staging repository."""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

API = "https://repository.apache.org/service/local"
SERVER = "apache.releases.https"
REPOSITORY = re.compile(r"orgapachehugegraph-[0-9]+")
PROFILE = re.compile(r"[0-9a-fA-F]+")


def credentials(settings_path):
    for server in ET.parse(settings_path).getroot().iter():
        if server.tag.split("}")[-1] != "server":
            continue
        fields = {entry.tag.split("}")[-1]: entry.text or "" for entry in server}
        if fields.get("id") != SERVER:
            continue
        values = []
        for name in ("username", "password"):
            value = fields.get(name, "")
            match = re.fullmatch(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}", value)
            if match:
                value = os.environ.get(match[1], "")
            if not value or "${" in value or value.startswith("{"):
                raise SystemExit("Maven staging credentials must be resolved plain values")
            values.append(value)
        return tuple(values)
    raise SystemExit(f"Maven settings lacks server {SERVER}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise SystemExit("Nexus redirects are not allowed")


class Nexus:
    def __init__(self, settings_path):
        username, password = credentials(settings_path)
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    def request(self, path, data=None):
        headers = {"Authorization": self.authorization, "Accept": "application/json"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps({"data": data}).encode()
        request = urllib.request.Request(API + path, data=body, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise SystemExit(f"Nexus request failed (HTTP {error.code})") from None
        except (urllib.error.URLError, TimeoutError):
            raise SystemExit("Nexus request failed (network error)") from None
        try:
            return json.loads(payload).get("data") if payload else None
        except (ValueError, AttributeError):
            raise SystemExit("Nexus returned an invalid response") from None


def valid_context(context):
    if not isinstance(context, dict) or not REPOSITORY.fullmatch(str(context.get("repository", ""))) or not PROFILE.fullmatch(str(context.get("profile", ""))):
        raise SystemExit("Invalid staging context")
    return context["profile"], context["repository"]


def start(nexus, record):
    profiles = nexus.request("/staging/profiles")
    matches = [item for item in profiles if item.get("name") == "org.apache.hugegraph"] if isinstance(profiles, list) else []
    if len(matches) != 1 or not PROFILE.fullmatch(str(matches[0].get("id", ""))):
        raise SystemExit("Expected one HugeGraph staging profile")
    profile = matches[0]["id"]
    result = nexus.request(f"/staging/profiles/{profile}/start", {"description": "HugeGraph GitHub Actions candidate"})
    context = {"profile": profile, "repository": result.get("stagedRepositoryId") if isinstance(result, dict) else None}
    valid_context(context)
    record.write_text(json.dumps(context) + "\n")
    repository = context["repository"]
    with open(os.environ["GITHUB_ENV"], "a") as output:
        output.write(f"MAVEN_DEPLOY_URL={API}/staging/deployByRepositoryId/{repository}\n")
    print(f"Created staging repository: {repository}")


def close(nexus, record):
    profile, repository = valid_context(json.loads(record.read_text()))
    path = f"/staging/repository/{repository}"

    def status():
        metadata = nexus.request(path)
        if not isinstance(metadata, dict) or metadata.get("repositoryId") != repository or metadata.get("profileId") != profile:
            raise SystemExit("Staging repository does not match the recorded context")
        return metadata.get("type"), metadata.get("transitioning")

    def wait_ready(closed_only=False):
        deadline = time.monotonic() + 300
        while True:
            state, transitioning = status()
            if state not in ("open", "closed") or not isinstance(transitioning, bool):
                raise SystemExit("Invalid staging repository state")
            if not transitioning and (state == "closed" or not closed_only):
                return state
            if time.monotonic() >= deadline:
                raise SystemExit("Staging close did not complete; inspect Nexus activity")
            time.sleep(15)

    # Wait for repository transitions, rather than guessing a fixed delay.
    if wait_ready() == "open":
        nexus.request(f"/staging/profiles/{profile}/finish", {"stagedRepositoryId": repository, "description": "Close HugeGraph candidate after successful upload"})
        wait_ready(closed_only=True)
    url = f"https://repository.apache.org/content/repositories/{repository}/"
    context = {"profile": profile, "repository": repository, "state": "closed", "url": url}
    record.write_text(json.dumps(context) + "\n")
    receipt = record.parent / "release-context.txt"
    with receipt.open("a") as output:
        output.write(f"staging_repository={repository}\nstaging_state=closed\nstaging_url={url}\n")
    print(f"Closed staging repository: {repository}\nCandidate URL: {url}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("start", "close"):
        raise SystemExit("Usage: maven-staging.py start|close SETTINGS")
    context_file = Path(os.environ["RUNNER_TEMP"]) / "maven-staging.json"
    globals()[sys.argv[1]](Nexus(sys.argv[2]), context_file)
