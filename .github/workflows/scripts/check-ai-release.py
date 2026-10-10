#!/usr/bin/env python3
"""Validate AI metadata without installing packages or executing setup.py."""

import ast
import os
import re
import subprocess
import tomllib
from pathlib import Path

expected = os.environ['RELEASE_VERSION']
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', expected):
    raise SystemExit('Use a numeric release version, without SNAPSHOT or RC suffix')

# Inspect metadata without executing setup.py or installing Python packages.
metadata = Path('pyproject.toml')
if metadata.is_file():
    with metadata.open('rb') as f:
        versions = [tomllib.load(f).get('project', {}).get('version')]
else:
    tree = ast.parse(Path('hugegraph-llm/setup.py').read_text())
    versions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        is_setup = (
            isinstance(function, ast.Attribute) and function.attr == 'setup'
            or isinstance(function, ast.Name) and function.id == 'setup'
        )
        if is_setup:
            for keyword in node.keywords:
                if keyword.arg == 'version' and isinstance(keyword.value, ast.Constant):
                    versions.append(keyword.value.value)

if versions != [expected]:
    raise SystemExit(f'Source version {versions!r} does not match {expected!r}')

sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
with open(os.environ['GITHUB_ENV'], 'a') as f:
    f.write(f'SOURCE_SHA={sha}\n')
receipt = Path(os.environ['RUNNER_TEMP']) / 'release-context.txt'
receipt.write_text(f'component=ai\nsource_sha={sha}\nversion={expected}\npackage=source-only\n')
print(receipt.read_text(), end='')
