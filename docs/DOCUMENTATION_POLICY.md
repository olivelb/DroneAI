# Documentation maintenance policy

## Authority order

When descriptions disagree, use this order:

1. executable source contracts, migrations and tests;
2. current normative documents listed in [`README.md`](README.md);
3. dated audit and benchmark evidence for the exact recorded revision;
4. plans, proposals and historical contracts.

Historical evidence is preserved because its hashes, datasets, hardware and
measurements are part of the engineering record. It must carry a date or an
explicit historical banner and must never be presented as the current
deployment procedure.

## Required maintenance

Every change to stages, artifact kinds, quality-profile identities, resource
classes, deployment security, routes or release gates must update the
corresponding current document in the same pull request. Removed runtime paths
must be removed from normative prose rather than described as compatibility
workers.

`make docs-check` is the minimum documentation gate. It checks tracked local
links, Mermaid fence/sequence structure and source-backed documentation
contracts. A documentation-only change selects the documentation job; changes
to an unknown path fail safe to the broader CI scopes.

Before merging a release change, reviewers must also check:

- commands and environment variables against the current scripts;
- diagrams against the current DAG and service topology;
- protected-environment examples for exact OCI digests, RLS, trusted proxy
  CIDRs, NetworkPolicy and external Secrets;
- historical claims for a revision, dataset, hardware and evidence link;
- current limitations without duplicating already implemented work.

The current verification disposition is
[`audits/2026-08-29-audit-verification.md`](audits/2026-08-29-audit-verification.md).
