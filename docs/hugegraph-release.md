# HugeGraph publisher workflows

These workflows are each release manager's personal publishing entrypoint. Use a
reviewed source ref and the Secrets already configured in this repository.

| Workflow | Build/deploy entrypoint | Candidate packages |
| --- | --- | --- |
| Server | Root Maven reactor; Java 17; version 1.8.0 | Source and binary |
| Toolchain | Root Maven reactor; Java 17; SDK 1.8.0 | Source and binary |
| Computer | `computer/` Maven reactor; existing 1.5.0 ref; Java 11 | Source |
| AI | Existing 1.5.0 ref; no PyPI publication | Source |

## Select the operation

Both upload switches default to `false`.

| `deploy_maven` | `deploy_svn` | Operation |
| --- | --- | --- |
| false | false | Public build without private settings, signing keys, or upload credentials |
| true | false | Signed Maven deployment to Apache staging |
| false | true | Build, prepare signed candidates, upload to SVN dev |
| true | true | Maven staging deployment followed by independent SVN dev upload |

AI only has the SVN switch. Maven deployments use the complete selected reactor,
including its distribution and test modules. They do not promote, release, or
close Nexus repositories. A successful Maven deployment followed by a failed SVN
upload remains a Maven upload; check the run before retrying either operation.

Deployment is explicitly per module (`deployAtEnd=false`). Apache parent 23's
deploy plugin 2.8.2 can silently leave its deferred queue unflushed when build
extensions create separate plugin realms. The CI file-repository regression
checks that the publisher configuration actually deploys every fixture POM,
rather than accepting Maven's exit status alone. A later reactor failure can
leave a partial staging upload; inspect that repository before retrying.

Use `repository_url`, `repository_branch` (branch, tag, or full SHA), and
`release_version` to choose the source. Server and Toolchain default to `master`;
Computer and AI preserve their existing release refs. A version mismatch fails
before upload. Each run records the resolved full source SHA and uses it for the
source archive as well as the build. No remote release branch is required.

SVN uploads call the selected source repository's existing `apache-release.sh`.
The workflow binds its local `release-VERSION` branch to the checked-out SHA;
it does not create a remote branch. Native scripts archive, sign, hash, verify,
and upload to `dev/hugegraph/VERSION`. Candidate RC subdirectories are deferred
until native scripts support them. Existing AI refs' tracing is removed from a
temporary copy beside the original before passing credentials.

## Maven staging and SDK provenance

For Toolchain, provide `staging_repository` when consuming a specific Apache
staging repository. Use its concrete repository URL, not the broad staging group.
The workflow resolves SDK dependencies in an isolated Maven local repository and
checks their repository origin. The mirror uses the existing Apache server ID
so Maven upload runs can authenticate without copying credentials; public builds
remain anonymous. No local Server installation substitutes for
remote staging consumption in the publisher workflow.

The Toolchain 1.8.0 build needs readable SDK 1.8.0 dependencies. Build-only means no
private publisher credentials; it does not make unpublished dependencies
available. Local same-source SDK builds used in development tests must be reported
as source prevalidation, not staging verification.

Before deployment, review the run's source SHA, version, repository ID and target. The effective POM must target
`apache.releases.https` at
`https://repository.apache.org/service/local/staging/deploy/maven2`; the Maven
settings must contain that server ID. The `stage` profile only adds a dependency
repository, and does not select a deploy target or authorize a promote operation.

After deployment, identify the resulting staging repository in Apache Nexus and
record its ID and concrete consumer URL with the workflow run. Do not assume that
an open staging repository is anonymously readable. Supply an accessible selected
repository to Doc validation; closing or promoting it is a separate operation.

## Existing Secrets

- `GPG_PRIVATE_KEY`: private signing key, imported only for publication.
- `M2_SETTINGS`: Maven settings and Maven authentication, used only for Maven
  deployment. SVN username/password are not assumed to be Maven credentials.
- `RELEASE_USERNAME`, `RELEASE_PASSWORD`: SVN authentication, used only by SVN
  write steps.

Keep settings, private keys and passphrases out of logs and workflow source. The
workflows do not create or migrate Secrets. The release manager must ensure the
existing signing configuration permits unattended Maven and archive signing;
encrypted-key/passphrase configuration is not inferred from Secret names.

## Verification and formal migration

The `Release CI checks` workflow runs actionlint, shellcheck, a local Maven
deployment regression, and real local SVN migration tests. It never writes to Apache
SVN or Maven. Full publisher build and staging results are separate evidence.

After upload, Doc validation verifies candidate signatures, SHA512, package
contents, the selected staging SDK, and core startup/runtime behavior. It neither
signs nor uploads candidates.

`Release SVN Packages` is the post-vote migration entrypoint. Select the exact
candidate `svn_path`; it moves that directory to `release/hugegraph/VERSION`.
Optional KEYS replacement copies from dev and preserves dev KEYS. Old-version
deletion is opt-in, cannot name the current version, and is committed with the
migration in one transaction. Never use this workflow for RC upload.

For a release rehearsal, first run build-only. Review explicit parameters before
credentialed uploads. Maven staging upload alone does not constitute an Apache
release, a successful vote, or verified SVN candidates.
