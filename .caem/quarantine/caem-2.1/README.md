# CAEM 2.1.0 — historical quarantine

The files in this directory are the CAEM 2.1.0 policy, repository profile,
repository registry, and schemas that used to be vendored directly under
`.caem/` in this repository.

**They are historical bytes, preserved unchanged, and they carry zero active
authority.** They are not consulted by any application code (no consumer was
found in `app/`, `scripts/`, or `tests/` before this quarantine — see
`docs/RI_A0_CAEM_REUSE_MATRIX.md`), and after this change nothing in this
repository may treat them as a live CAEM identity, a source of reason codes,
or a substitute for the CAEM 3.0 F0 registry/manifest.

The single active source of CAEM identity for this repository is:

```text
config/caem/caem-3.0-f0.pin.json
```

See `metadata.json` in this directory for the machine-readable quarantine
record. Preserved here, unmodified, for provenance and rollback only:

- `policy.json` — CAEM 2.1.0 policy (`canonical-baseline`, released 2026-07-30)
- `repository-profile.json` — `caem_version: "2.1.0"` repository profile
- `repository-registry.json` — CAEM 2.1.0 repository registry
- `schemas/*.json` — the 8 CAEM 2.1.0 schema files this repository vendored
