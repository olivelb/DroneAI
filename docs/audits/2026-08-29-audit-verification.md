# Verification of the external repository audit - 29 August 2026

## Scope and verdict

Authoritative checkout: Ubuntu WSL2, /home/olivier/droneAI. Baseline:
`4bc24d30037fee67eb71ae089412a379a5acb5cd`, merge of PR #292, initially clean.
Changes are local on `codex/audit-security-gates`; no push, branch-protection
change, registry publication, Terraform apply or cluster deployment was made.

The audit identifies real CI and artifact-promotion gaps. Its numerical score
is subjective, and its conclusion is not a production qualification. One
functional finding is obsolete: standalone analyses already run as bounded
detection Stage Jobs in the baseline. An intermediate row in the previous
cleanup audit caused the ambiguity and is corrected here.

GitHub was checked read-only using the authenticated WSL gh CLI:

- main required checks: `CI gate` and `CUDA validation gate`; strict=true.
- No repository variables were returned, including no `DRONEGS_GPU_CI`.
  This is not proof that no runner exists.
- CodeQL default setup returned `state: not-configured`.
- The repository is public. The added CodeQL action is pinned to the commit
  resolved from the upstream v4 annotated tag:
  `cdf488f595d80d6e07e03d4674febd5ab45fa938`.

## Finding ledger

| Finding | Verification against baseline | Change / remaining acceptance |
| --- | --- | --- |
| Architecture and standalone analysis | Stage Jobs confirmed. Kafka-only standalone path is obsolete: create/retry call queue_analysis_stage, backed by shared/analysis_stages.py; disabled Stage Jobs yield 503. | No runtime rewrite. Corrected the stale intermediate audit row. Existing analysis tests and browser journeys retained. |
| Tenant isolation / RLS | Confirmed in shared/database.py: organization/credential settings are transaction-local. Protected chart guards require RLS and separate roles. | No change to the security boundary. CPU tests rerun; real PostgreSQL isolation was not requalified in this task. |
| Authentication / sessions | HMAC credentials, compare_digest, cookie and Origin checks are present. Session-key rotation has no overlapping key-ring mechanism. | Existing protections retained; a rotating key ring remains an optional future contract, not an established vulnerability. |
| Uploads / object storage | Provider-state multipart checks and immutable storage helpers are present; Terraform assets policy spans the asset bucket. | No bucket/IAM mutation. Stage-specific Secrets do not establish stage-specific IAM permissions. Prefix policy changes need an inventory of actual input/output/shared-manifest keys. |
| Pod security / NetworkPolicy | Hardened Stage Job templates confirmed; no NetworkPolicy resources in the baseline. | Added opt-in, protected-overlay default-deny policies targeted to API/control/frontend and dynamic Stage Jobs, with namespace/port allowlists. Stage Jobs receive no ingress, Kafka allowance or service-account token. Live CNI, DNS, ingress, metrics and external-endpoint probes remain required; portable HTTPS egress is destination-agnostic. |
| Physical GPU gate | GPU job can be skipped for a missing variable or fork; there was no aggregate GPU gate and it was not required by main protection. | Added fail-closed GPU qualification gate. Native build/CTest input changes require real success. Provision a trusted runner, enable the variable, run the workflow, then make this third check required. Remote settings deliberately unchanged. |
| CUDA change selection | Confirmed: baseline checked selected version lines, missing flags, source/header, hash and other Dockerfile changes. | Replaced by shared path/dependency classification, including deleted/renamed inputs and selection/harness changes. Unknown events, malformed SHA ranges and Git failures cannot grant an exemption. |
| Aggregate CI gates | Confirmed: unconditional success-or-skipped acceptance did not bind a result to the selector output. | CI, CUDA and GPU gates now require a successful selector, explicit true/false output, success for required jobs and skipped only for an explicit exemption. |
| Mutable protected application tags | Confirmed: 7-40 hex tags passed the Helm helper. Preprod did not even set the optional global guard. | Staging/production now require OCI SHA-256 digests regardless of that flag. Both overlays and CI renders migrated; local development keeps its tag contract. |
| Manual publication / provenance | Confirmed: arbitrary abbreviated SHA and local base tag reuse were possible. No signed promotion workflow. | Local publisher requires full clean HEAD identity and validates reused-base labels. Added a tag-only hosted promotion that requires exact successful CI/CUDA/GPU/CodeQL runs, rebuilds five GHCR images, repeats SBOM/Trivy gates, emits BuildKit and GitHub provenance, signs every digest and the release manifest through Sigstore OIDC, and binds qualification URLs. External acceptance: configure required reviewers on `production-promotion` and execute the first real release; the workflow does not deploy or replace target drills. |
| Image CVEs | The previous PR records API unfixed findings and fixable-CVE gates; these are historical scan results. | No new scan was run, so 14 HIGH / 3 CRITICAL is not asserted as the current count. Refresh exact image digests against the current advisory DB and attach owner/expiry to any release risk acceptance. |
| Browser headers | Empty Next config confirmed. | Added enforced base/object/frame restrictions, nosniff, referrer/permissions/HSTS and disabled powered-by. Full CSP is report-only pending nonce/hash, origin and GPU-viewer qualification; no report collection endpoint yet. API/Ingress-wide HSTS remains an operator contract. |
| Client IP / rate limits | request.client.host is used and no explicit trusted proxy list is supplied. A shared proxy bucket is a plausible deployment issue, not an observed production failure here. | Helm now requires explicit narrow trusted proxy CIDRs in protected environments and passes them to Uvicorn; wildcard, internet-wide, placeholder and whitespace-bearing values fail rendering. The actual Traefik/LB peers plus spoofed-header and two-client tests remain target-cluster acceptance. |
| Source SAST | No workflow and GitHub default setup not configured. | Added pinned CodeQL Python and JS/TS security-extended workflow with restricted permissions, no self-hosted runner or secrets. PR and merge-queue diffs select only affected languages; malformed/unknown source input selects both, and manual dispatch scans both. Not executed on GitHub yet; alert review and merge policy remain external acceptance. |
| Coverage / PlayCanvas maintenance | 60% gate and guarded PlayCanvas patch scripts confirmed. | Raised the branch-coverage floor to 65%, below the measured 70% result but no longer leaving a ten-point regression window. Reaching 75% still requires deliberate critical-path tests. Existing patch invariants/tests remain; no speculative fork or packaging rewrite. |
| Operations / licences | Release evidence tooling and backup/restore runbooks exist; MIT/GPL/AGPL/SAM/NVIDIA notices require distinct review. | No target release or legal clearance claimed. Target-GPU inference/scientific acceptance, interruption, restore, rollback, licence/redistribution approval and artifact retention remain release gates. |

## Implementation and rollout notes

### CI selection

Candidate diffs are extracted by
[the fail-safe event helper](../../scripts/ci/changed_paths.py) for both pull
requests and merge groups. The general CI, CodeQL, hosted CUDA build and native
GPU selectors each map paths to the work they actually exercise. Hosted CUDA
uses the complete Docker build context; physical GPU qualification uses the
narrower native/runtime closure. A change to either selection policy selects
its own validation, while unrelated application or prose changes stay exempt.
Malformed events, invalid SHAs, Git failures and unknown general-CI paths select
the conservative full fallback. This gate qualifies the
existing DroneGS/CUDA suite; it does **not** certify SAM/YOLO inference,
scientific accuracy or every production GPU architecture.

[The aggregate checker](../../scripts/ci/check_selected_jobs.py) consumes the
structured GitHub needs object. A missing GPU runner configuration or unsafe
fork execution produces a failing selected gate, never fabricated evidence.
The existing fork restriction remains intact. Source changes must still be
reviewed, and GitHub must make the new GPU check required for it to block merge.

### Image migration

Existing staging/production tag references will fail the next Helm render.
Populate dashboardApi.image and dashboardFrontend.image with the exact
published digest references; migrations/control workers use the API image
through the same helper. Keep global.imageRegistry empty when the references
already include the registry host. Stage executor digests remain mandatory.

The cloud CPU helper accepts API_IMAGE, FRONTEND_IMAGE and RELEASE_VALUES_FILE.
It rejects the retired IMAGE_TAG variable and partial/malformed image inputs.
Without new image arguments it reuses deployed digests and checks API/control
consistency. This is not a deployment or registry promotion performed here.

Reused local CUDA base images without the matching revision label are now
rejected; rebuild them explicitly with the publisher when appropriate. A
forged label is still possible with control of the local daemon. End-to-end
signed promotion remains necessary before unattended production rollout.

### Signed promotion

The signed workflow is intentionally tag-only. It verifies a signed annotated
tag through GitHub, `main` ancestry and synchronized SemVer before any push.
Four successful runs for the exact commit are mandatory: general CI, hosted
CUDA containers, manually dispatched physical GPU qualification and CodeQL.
The approved build then publishes five GHCR digest identities, fails on fixable
HIGH/CRITICAL findings, records CycloneDX and scanner hashes, publishes
BuildKit/GitHub attestations, verifies keyless image signatures and signs the
assembled manifest. The external GitHub environment reviewer policy and the
first live workflow execution remain unverified in this local task.

### Browser policy

The enforced policy intentionally omits script-src until Next hydration has
nonce/hash support. The report-only policy allows blob workers but will report
inline scripts and unqualified origins in browser diagnostics. Do not describe
it as complete XSS protection. Header and mission E2E tests run against a fresh
production Next build with mocked API journeys; they do not replace real
WebGPU/S3/Ingress qualification.

References:
[Next.js headers](https://nextjs.org/docs/app/api-reference/config/next-config-js/headers),
[Uvicorn proxy settings](https://www.uvicorn.org/settings/),
[GitHub advanced CodeQL setup](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configuring-advanced-setup-for-code-scanning).

## Validation evidence

Local environment: Ubuntu WSL2, Python 3.12, repository virtualenv,
Next.js 16.3.1; commands run in the authoritative checkout.

- Targeted selector/gate, supply-chain, Helm render and publication regression
  suites passed. Helm tests reject 7/40-character Git tags even when the global
  guard is disabled, and render both protected overlays with digests.
- Publication tests use an isolated clean Git fixture and a mocked Docker CLI:
  full-SHA mismatch, abbreviated SHA, dirty worktree and mismatched base labels
  are rejected; CPU-only and explicit GPU paths remain covered.
- Frontend: 380 unit tests, lint, typecheck and production build passed.
- Chromium E2E: 13 passed, including HTTP headers, auth, mission lifecycle,
  standalone analysis creation/cancellation/retry and downloads.
- Full make static passed, including strict typing, Ruff, ShellCheck,
  actionlint, document links, schema and platform-version contracts.
- Additional strict mypy passed for the new shared classifier and gate helper.
- Final full CPU coverage run: **1648 passed, 1 skipped, 45 deselected** in
  44.14 seconds; **70% branch-enabled coverage**. No threshold was lowered.
- Final make static, extra strict mypy and git diff --check passed.
- All seven Mermaid blocks rendered successfully with Mermaid CLI 11.16.0;
  the GitHub sequence-diagram parse regression is also covered by docs-check.

Evidence files are retained under tmp/audit-verification-20260829/:
coverage.log, coverage.data, static.log, coverage-final.log,
coverage-final.data, static-final.log, coverage-continuation.data and
coverage-final-continuation.data. They are not committed artifacts.
No GPU/native build, physical GPU run, image/CVE scan, CodeQL execution,
PostgreSQL integration, live cloud network check or production drill was run.
These are separate and explicitly unqualified.
