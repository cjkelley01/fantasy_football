# Local V1 Release Checklist

This checklist supports review; it does not declare a release.

## Automated credential-free gate

After `setup-local.ps1`, run:

```powershell
.\scripts\verify-release.ps1
```

It runs the complete Python and frontend suites, TypeScript typecheck, production build, Python compilation, Ruff correctness rules (`E9,F63,F7,F82`), Python dependency consistency, production npm vulnerability audit, documentation/API/security tests included in pytest, and Git whitespace validation. It performs no ESPN request and does not alter user databases. The repository does not currently claim a full-style Ruff baseline.

The disposable launcher/process tier is explicit because it creates and terminates launcher-owned test processes:

```powershell
.\scripts\verify-release.ps1 -IncludeWindowsProcessTests
```

## Reviewer signoff

- [ ] Revision and working tree recorded.
- [ ] Clean setup and production-local start succeed on Windows.
- [ ] Health identity matches the launcher metadata and static assets come from `frontend/dist`.
- [ ] Sanitized refresh/snapshot workflow or accepted live evidence reconciled.
- [ ] Baseline, action search, manual scenario, retained history, and restart smoke succeed.
- [ ] Statistical incompleteness and capability limitations remain visible.
- [ ] No P0 or P1 finding remains open.
- [ ] Known limitations and recovery procedure reviewed.
- [ ] Private data, credentials, databases, and temporary artifacts are absent from Git.
- [ ] Independent reviewer decides release disposition.
