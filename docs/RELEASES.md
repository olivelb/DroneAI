# DroneAI platform releases

## Version identity

DroneAI uses Semantic Versioning for the platform as a whole. The canonical
version is the single line in [`VERSION`](../VERSION). Until the public
application and deployment contracts are declared stable, releases remain in
the `0.y.z` series:

- `PATCH` contains backward-compatible fixes and qualification updates;
- `MINOR` may change pre-1.0 application, event, database or deployment
  contracts and must document the required migration;
- `MAJOR` is reserved for the first stable contract and later incompatible
  stable changes.

Platform tags use `vMAJOR.MINOR.PATCH`. DroneGS remains a separately versioned
native component whose tags use `dronegs-vMAJOR.MINOR.PATCH`; a DroneGS tag is
not a DroneAI platform release.

The canonical version is synchronized with:

- `pyproject.toml` project metadata;
- the frontend `package.json` and root lockfile package;
- the Helm chart `version` and `appVersion`.

Run `python tools/check_platform_version.py` after changing any of these
surfaces. Pull-request CI enforces the same command. A platform tag also runs a
lightweight tag-to-version contract workflow; it does not rebuild COLMAP or
CUDA images.

## Artifact identity

The human-readable platform version does not replace immutable deployment
identity. Production and preproduction application images remain pinned by a
Git commit tag or, preferably, an OCI digest. Release evidence must record the
platform version, Git commit, image digests, schema migration head and the
qualified DroneGS version/commit.

Scientific qualification is scoped to an exact commit. Existing CUDA, GPU and
full E2E policies still apply: a platform version change alone does not trigger
long builds or GPU tests, while changes to CUDA/COLMAP versions, GPU
architecture support, CTest contracts or product-critical Gaussian behavior
require the corresponding explicit qualification.

## Release procedure

1. Start from a clean, protected `main` containing only reviewed and green
   pull requests.
2. Choose the next SemVer version and update `VERSION`, Python metadata,
   frontend package/lock metadata and both Helm versions in one reviewable PR.
3. Add or update migration and operator notes for every changed contract.
4. Run `make check`; complete any explicitly required CUDA/GPU/E2E
   qualification and retain its commit-scoped evidence.
5. Merge the version PR, then create the signed or annotated tag `vX.Y.Z` on
   that exact merge commit.
6. Confirm the platform release contract workflow is green before publishing
   release notes or promoting the immutable image digests.
7. Deploy by digest or commit tag, run smoke checks, and retain a documented
   rollback target.

Never move or reuse a published platform tag. A failed candidate receives a
new patch version after its corrective PR; deployment rollback points to the
previous immutable artifacts rather than rewriting history.
