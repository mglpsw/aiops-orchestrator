# .caem

This directory no longer holds active CAEM authority. The CAEM 2.1.0
`policy.json`, repository profile, repository registry, and schemas have been
moved to `quarantine/caem-2.1/` (read-only, `authority_effect: none` —
see `quarantine/caem-2.1/metadata.json`).

The single active source of CAEM identity for this repository is
`config/caem/caem-3.0-f0.pin.json`, pinning the CAEM 3.0 F0 interface
(`mglpsw/caem`, carrier `28ca73f338417b5c7e9275c6154b6a0eddbb8bc7`,
`maturity: development_freeze`, `published: false`). It is verified by
`app.caem_consumer.f0` / `scripts/verify-caem-f0-pin.py`.

Artefatos sanitizados podem ser adicionados em subdiretórios; segredos e dados clínicos identificáveis são proibidos.
