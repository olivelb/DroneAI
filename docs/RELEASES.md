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
full OCI SHA-256 digest. A Git commit tag is provenance metadata, not an
immutable registry reference; Helm rejects tags in staging and production even
when the optional local immutable-image guard is disabled. Release evidence must record the
platform version, Git commit, image digests, schema migration head and the
qualified DroneGS version/commit.

Scientific qualification is scoped to an exact commit. Existing CUDA, GPU and
full E2E policies still apply: a platform version change alone does not trigger
long builds or GPU tests, while any change to the CUDA build dependency closure (Dockerfiles, native
sources/headers, CMake, GPU locks and validation scripts) requires hosted build
and native GPU qualification. Product-level AI inference and scientific
accuracy still require their separate target-environment qualification.

## Release procedure

1. Start from a clean, protected `main` containing only reviewed and green
   pull requests.
2. Choose the next SemVer version and update `VERSION`, Python metadata,
   frontend package/lock metadata and both Helm versions in one reviewable PR.
3. Add or update migration and operator notes for every changed contract.
4. Run `make check`; complete any explicitly required CUDA/GPU/E2E
   qualification and retain its commit-scoped evidence.
5. Merge the version PR, then create a signed annotated tag `vX.Y.Z` on
   that exact merge commit.
6. Before tagging, manually run CI, CUDA container validation, physical GPU
   qualification and CodeQL against that exact `main` commit; all four workflows
   must finish successfully.
7. Create the signed tag. The platform contract and signed-promotion workflows
   start from that tag; approve `production-promotion` only after reviewing the
   commit-scoped evidence.
8. Deploy only the manifest's signed OCI digests, run smoke checks, and retain
   a documented rollback target.

Never move or reuse a published platform tag. A failed candidate receives a
new patch version after its corrective PR; deployment rollback points to the
previous immutable artifacts rather than rewriting history.

## Signed promotion boundary

`.github/workflows/promote-images.yml` runs only for a platform tag. Its
preflight rejects lightweight, unsigned or GitHub-unverified tags, version
drift and commits outside `main`. It also queries GitHub for successful
commit-scoped runs of CI, CUDA container validation, physical GPU qualification
and CodeQL. Run those workflows manually on the release commit before creating
the tag; a skipped physical GPU job cannot satisfy promotion.

After approval through the `production-promotion` GitHub environment, hosted
builders create the API, frontend, IA, CUDA/COLMAP base and COLMAP runtime
images in `ghcr.io/<owner>/droneai`. The runtime consumes the exact base digest.
Every image receives a BuildKit max-mode provenance/SBOM attestation, a
CycloneDX SBOM, a complete HIGH/CRITICAL Trivy report, a fixable-CVE gate, a
GitHub artifact attestation and a keyless Sigstore signature. The final
keyless-signed manifest binds all image digests, SBOM/report hashes and
qualification run identities to one 40-character commit and platform tag.

Repository code cannot create or enforce environment reviewers. Before the
first release, configure `production-promotion` with required reviewers and
protect package visibility/retention. The workflow publishes and records
artifacts; it does not deploy Helm, mutate a cluster or replace target-specific
interruption, restore, rollback, inference and scientific acceptance.

The local preproduction publisher remains available for explicit development
work. Its revision labels are useful diagnostics but are not cryptographic
provenance and must not substitute for the signed promotion path.
The [29 August audit verification](audits/2026-08-29-audit-verification.md)
records the implemented safeguards, local evidence and unresolved release gates.
