# Releasing

`perceivo` is released to PyPI from this repository by `.github/workflows/release.yml`,
through PyPI trusted publishing, TestPyPI first. No package upload happens any other
way: there is no API token, and nothing is uploaded from a workstation.

## Source and artifact preflight

A release candidate must pass every repository check from one exact commit:

1. Verify `SOURCE-PROVENANCE.json` and the closed repository boundary.
2. Pass Ruff lint and format checks with no outstanding diagnostics.
3. Pass scoped mypy and consumer typing fixtures with no outstanding diagnostics.
4. Pass the public unit suite on Python 3.11, 3.12, and 3.13 at the declared
   coverage floor of at least 95 percent.
5. Build a wheel and source distribution in the canonical Linux job described
   below, with `SOURCE_DATE_EPOCH` taken from the archived source commit.
6. Pass `twine check`, archive membership checks, the two-build member/hash
   comparison, and the two-build raw archive SHA-256 comparison.
7. Install the wheel and source distribution separately in empty environments;
   verify metadata, version, import origin, CLI help/version, authoring imports,
   and the approved local quickstart.
8. Pass the maintainer's review: of the exact tree and provenance record before
   the release branch is pushed, then of the CI evidence and artifacts its pull
   request produces. The release pull request needs no approving review.

CI runs these checks without upload credentials. A failing or skipped required
check blocks the release candidate.

## The canonical Linux build

**Only the artifacts produced by `.github/workflows/build-distributions.yml` are
release candidates.** `ci.yml` calls it on every pull request and push to `main`,
and `release.yml` calls it on a release tag, so a release uploads the output of
exactly the job definition CI exercises. A build produced anywhere else — including a
maintainer's Windows or macOS workstation — is a smoke test. Its archive hashes
are not comparable to the candidate hashes and must never be quoted as
reproduction evidence.

The workflow fixes the release-candidate build to one operating-system release
label, one interpreter patch release and exact Python build-tool versions. The
hosted runner image behind `ubuntu-24.04` remains GitHub-managed and can change
within that OS release, so the workflow records and compares artifacts produced
within each run rather than treating the label as an immutable image identity.

| Input | Value | Why it is pinned |
|---|---|---|
| Runner OS label | `ubuntu-24.04` | Selects the Ubuntu release explicitly; it does not pin an immutable hosted-runner image. |
| Interpreter | Python `3.12.14` | A patch bump changes `zipfile`, `tarfile` and `gzip` behaviour. |
| Build backend | `setuptools==82.0.1`, `wheel==0.48.0` | Different setuptools releases emit different archive metadata. |
| Front end and check tool | `build==1.6.1`, `twine==7.0.0` | Keeps the archive layout and metadata checks fixed. |
| Isolation | `python -m build --no-isolation` | Without it `build` resolves its own backend and the pins above do not apply. |
| `SOURCE_DATE_EPOCH` | `git show -s --format=%ct HEAD` of the **archived source commit** | See below. |

### Why the epoch comes from the archived source head

The job checks out `github.event.pull_request.head.sha` on a pull request and
`github.sha` on a push, so `HEAD` is the commit that `git archive` exports. The
default pull-request checkout is a synthetic merge commit whose commit time is
minted when the merge ref is created, so re-running the same job would stamp a
different epoch into the same source and produce different artifacts. Taking the
epoch from the archived head removes the synthetic merge commit's changing time.
Artifact bytes still depend on the pinned tools and the canonical job
environment as well as the source commit.

### Why the archives are canonicalized

With the pins above, on Linux:

- The **wheel** is byte-identical between two clean builds before
  canonicalization. Canonicalization still normalizes timestamps, modes, creating
  system, external attributes and entry order before the within-job raw-hash
  comparison.
- The **sdist is not** byte-identical between two clean builds even in one
  container, and `compare-archives` — which hashes member payloads only —
  **passes on non-reproducible sdists**. The member comparison is a real
  control, but it is blind to the tar and gzip metadata that differs, so the raw
  archive SHA-256 comparison is asserted alongside it, not instead of it.

`scripts/verify_public_tree.py canonicalize-sdist` and `canonicalize-wheel`
rewrite an archive with member names and payload bytes preserved exactly, and
with ownership, modes (executable bit kept), modification times, the gzip header
and the zip external attributes normalized. They validate every member first and
stage the rewrite in a temporary file, so an archive carrying an absolute path,
a `..` segment, a drive qualifier, a duplicate name, a link or a device node is
rejected with the input left byte-identical.

### Local build scope

Local and container builds are smoke checks: they can confirm that the pinned
tools build the exact source, that metadata passes `twine check`, and that two
clean builds converge under the canonicalizers. Installing those tools may
require downloading their packages and resolver dependencies.

Only the canonical CI job produces release candidates. A different operating
system, container base, hosted-runner revision or compression library can
produce different archive bytes, so cross-host hash equality is not promised
and local hashes are not candidate reproduction evidence.

## Release sequence

1. **Source.** Each release is one commit on a release branch, merged to `main`
   by a pull request with the rebase method, so `main` gains exactly that commit
   on top of the previous release. The release pull request sets `VERSION` and
   the `pyproject.toml` version to the release version and carries the
   `CHANGELOG.md` section. Its tree
   is produced by the maintainers' release tooling from two full commit SHAs, in
   clean clones, with no network and no push. The tooling exits 1 when:
   - a file the export emits was hand-edited here (origin `public-destination`),
     unless the operator passes exactly one of `--replace-override <path>` or
     `--keep-override <path>`; the pull request body quotes the choice;
   - a file the export does not emit is recorded with origin `destination`;
   - a file here has no provenance row.

   It never silently overwrites a `public-destination` file and never restores a
   file the source deleted.
2. **Settings read-back.** Immediately before the tag push, run
   `python scripts/check_release_settings.py check --reviewer <release reviewer>
   --required-checks <names>`. If it exits 1, **do not tag**. It also exits 1 when
   the `main-protection` pull request rule requires an approving review or a code
   owner review, or allows a merge method other than rebase. To refuse further
   reviewer logins, set `RELEASE_FORBIDDEN_REVIEWERS` to a comma-separated list.
3. **Tag.** Push `v<VERSION>` (for example `v1.0.0rc1` or `v1.0.0`) on the commit
   the release pull request adds to `main`, which is `main`'s tip after the rebase
   merge. The rebase gives that commit a new SHA but not a new tree, and the
   provenance seal binds the tree. `scripts/check_release_tag.py` refuses a tag
   that is not in PEP 440 normal form, that differs from `VERSION` or the
   `pyproject.toml` version, or whose commit is not on `main`.
4. **Publish.** `release.yml` runs `verify` → `build` → `install-matrix`
   (Python 3.11, 3.12 and 3.13) → `testpypi` → `testpypi-smoke` → `pypi` (waits
   for the reviewer) → `pypi-verify` → `github-release`. The `build` job records
   each file's name and sha256 as **job outputs**. Every job that handles the
   files, and every upload, first checks them byte for byte against those
   outputs. Run artifacts are only carriers: any later job in the run can replace
   an artifact, but no job can change another job's outputs, so no digest is ever
   read from an artifact (`check_workflow_policy.py` enforces this).
   `testpypi-smoke` downloads what TestPyPI serves, proves each file's sha256
   equals the built one (`sha256 match <file> <sha256>`), and installs that wheel
   with its dependencies from pypi.org only. `SHA256SUMS` on the GitHub release is
   written from the same outputs.
5. **Approve** (below).

A `workflow_dispatch` run of `release.yml` is a dry run: it verifies, builds and
installs, and every upload job is skipped.

## The upload gate

`release.yml` is read from the tagged commit, so the checks inside it are mistake
guards. What decides whether bytes reach PyPI is:

1. the `release-tags` ruleset, which decides who may create a `v*` tag;
2. the `pypi` environment: exactly one required reviewer, a human account that no
   automation uses; "Prevent self-review" on; administrators may **not** bypass;
   deployments limited to tags `v*`;
3. the PyPI trusted publisher, bound to this repository, `release.yml` **and** the
   environment `pypi` (on TestPyPI, `testpypi`). A publisher with a blank
   environment would let any job of `release.yml` upload without a reviewer.

`scripts/check_release_settings.py check` reads 1 and 2 back. It only reads, and
never changes a setting. The publisher binding is read back by hand on pypi.org
and test.pypi.org.

Known limits:
- A repository administrator can change or delete an environment. A deleted
  environment is recreated **unprotected** by the next job that names it, so the
  environment is never deleted.
- The tag ruleset admits repository administrators.

Both are settings changes recorded in the audit log, and the reviewer approves
only a `pypi` job that is waiting.

### Adding required checks to `main-protection`

Edit the ruleset only from a fresh read, guarded:

1. `python scripts/check_release_settings.py ruleset-snapshot main-protection > snapshot.json`
2. Immediately before the edit, run
   `python scripts/check_release_settings.py ruleset-guard --pre snapshot.json`.
   If it exits 1, the ruleset has changed: take a new snapshot and start again.
3. Edit the freshly loaded ruleset, or PUT the full ruleset with the checks appended.
4. `python scripts/check_release_settings.py ruleset-verify --pre snapshot.json --add <names>`
   asserts that the only change is the added checks.

The guard cannot see a change made between its read and the save. That includes a
change made while an editor has the ruleset open in the browser, so keep that
interval short.

## Approving a PyPI deployment

Signed in as the release reviewer:

1. On the release pull request, note the commit it added to `main` (`main`'s tip
   after the rebase merge), and confirm the Publish run's commit equals it. If it
   does not, **Reject**.
2. Confirm `testpypi-smoke` is green on all three Python versions, with a
   `sha256 match` line for the wheel and one for the sdist.
3. Review deployments → `pypi` → **Approve and deploy**.

If anything is red or missing, or the `pypi` job was **not** waiting for review,
**Reject** and report it.

## Yank and stop-uploads runbooks

- **Yank.** On pypi.org, open the release → Options → Yank, and give a reason. A
  PyPI file can never be replaced:
  - a bad release candidate is followed by the next `rcN`;
  - a bad final release is yanked and followed by the next patch release.
- **Stop uploads.** Remove the GitHub trusted publisher on pypi.org (and on
  test.pypi.org), or set the `pypi` environment's tag rule to a pattern no tag
  matches while keeping its reviewer. **Never delete the environment.**

## Release policy

| Item | Policy |
|---|---|
| Supported security-fix versions | The latest minor release of the latest major version only (`SECURITY.md`) |
| PyPI reviewer | A human-only account that no automation uses is the sole required reviewer of `pypi`, with self-review prevented |
| Repository visibility | Public; releases are tagged on `main` |
| License | Apache-2.0 |

## Publication boundary

The workflow publishes only the `perceivo` distribution. Client and platform
source, artifacts and credentials are outside this repository.
