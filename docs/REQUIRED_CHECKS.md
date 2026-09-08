# Required merge qualification

The policy in `.github/required-checks.json` requires CI, CUDA, CodeQL, physical
GPU qualification and the native Windows viewer gate. Every gate reports on
pull requests and merge groups. Scope selectors deliberately skip unrelated
expensive jobs; missing or failed selection and skipped required jobs fail closed.

After the workflow PR is green and merged, inspect then apply the policy from
the authoritative WSL checkout with authenticated gh:

```sh
python3 tools/enforce_required_checks.py
python3 tools/enforce_required_checks.py --apply
```

The update preserves strictness and every existing required check, including
custom app bindings, and verifies the remote result. It does not bypass checks
or alter review requirements. GPU changes still need the configured trusted
physical runner; missing GPU capacity cannot become a successful exemption.
The Windows build runs for native sources, GSTile producers/contracts, common
corpus fixtures and selector/gate changes, including manual qualification.
