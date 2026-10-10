#!/usr/bin/env python3
"""Select the SDK candidate repository, reusing the Apache settings server id."""

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

url = os.environ['STAGING_REPOSITORY'].rstrip('/')
if not re.fullmatch(r'https://repository\.apache\.org/content/repositories/orgapachehugegraph-[0-9]+', url):
    raise SystemExit('Specify an individual Apache HugeGraph staging repository')

path = Path(os.environ['RUNNER_TEMP']) / 'release-settings.xml'
tree = ET.parse(path)
root = tree.getroot()
namespace = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''
if namespace:
    ET.register_namespace('', namespace[1:-1])

mirrors = root.find(namespace + 'mirrors')
if mirrors is None:
    mirrors = ET.SubElement(root, namespace + 'mirrors')
for mirror in list(mirrors):
    if mirror.findtext(namespace + 'mirrorOf') == 'staged-releases':
        mirrors.remove(mirror)

# Reuse the Apache server id: upload runs can authenticate without
# duplicating credentials; public builds still have no server entries.
mirror = ET.SubElement(mirrors, namespace + 'mirror')
for key, value in [('id', 'apache.releases.https'), ('mirrorOf', 'staged-releases'), ('url', url + '/')]:
    ET.SubElement(mirror, namespace + key).text = value
tree.write(path, encoding='utf-8', xml_declaration=True)
print(f'SDK staging repository: {url}')

# Resolver may qualify the repository id with the SHA1 of its canonical URL.
repository_key = "apache.releases.https-" + hashlib.sha1((url + "/").encode()).hexdigest()
with open(os.environ["GITHUB_ENV"], "a") as env:
    env.write(f"SDK_REPOSITORY_KEY={repository_key}\n")
