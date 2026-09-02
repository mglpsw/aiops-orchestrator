#!/usr/bin/env python3
"""Emit a real, cross-validated AgentReview v2 PayloadSetV2 (issue #132).

Reads a ManifestV2 and a directory of already-built ChunkPayloadV2 files
(one per chunk, any filenames), emits the resulting PayloadSetV2 --
fail-closed if the payloads do not exactly, coherently match the manifest
(see app.agent_review.payload_set_emission_v2 for the full cross-validation
chain). Never invented here: this CLI is thin wiring around
emit_payload_set_v2, not a second implementation of it.

--contract-version v2 is required and explicit, per the CLI naming
decision registered in #102: this is a NEW v2 CLI script (no v1 namesake
to collide with), using the "-v2" suffix convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.agent_review.contracts_v2 import ChunkPayloadV2  # noqa: E402
from app.agent_review.external_path_ingress_v2 import (  # noqa: E402
    ExternalPathIngressError,
    validate_external_input_directory_v2,
    validate_external_input_file_v2,
)
from app.agent_review.manifest_v2 import ManifestV2  # noqa: E402
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2  # noqa: E402
from app.agent_review.payload_set_v2 import PayloadSetBindingError  # noqa: E402

CONTRACT_VERSION_MISSING_REASON_V2 = "contract_version_required"
MANIFEST_INVALID_REASON_V2 = "manifest_invalid"
PAYLOAD_INVALID_REASON_V2 = "payload_invalid"
NO_PAYLOADS_FOUND_REASON_V2 = "no_payloads_found"
# G4B (#200-G4B): `--payloads-dir` -- the "responses directory" ingress
# shape for this CLI -- was enumerated with a completely unguarded
# `payloads_dir.glob("*.json")`: a symlink loop, an overlong path, or
# `--payloads-dir` pointed at a file rather than a directory each raised raw
# here, before a single payload was ever read.
#
# Post-merge Codex P2: the `.json` filter itself must key off each entry's
# own caller-visible name (`entry.entry_name`), not the symlink-resolved
# target's name (`entry.resolved_path.name`) -- otherwise `payload.json`
# and an `alias.txt -> payload.json` symlink both count as `.json` entries
# (duplicate), while an `alias.json -> payload.txt` symlink stops counting
# as one even though its own name says `.json`. Resolution still happens
# (via `iter_input_files()`) for the actual read/containment-safety check.
PAYLOADS_DIR_UNUSABLE_REASON_V2 = "payloads_dir_unusable"
OUTPUT_WRITE_FAILED_REASON_V2 = "output_write_failed"


class PayloadSetCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-version", required=True, help="must be exactly 'v2'")
    parser.add_argument("--manifest", required=True, help="path to a ManifestV2 JSON file")
    parser.add_argument(
        "--payloads-dir", required=True, help="directory of ChunkPayloadV2 JSON files, one per chunk"
    )
    parser.add_argument("--output", required=True, help="path to write the emitted PayloadSetV2 JSON")
    return parser.parse_args(argv)


def _load_manifest(path: Path) -> ManifestV2:
    # G4B: `--manifest` is a raw caller-controlled path, read through the
    # central external-path ingress authority rather than a bare
    # `Path.read_text()` -- missing/wrong-type/unreadable/symlink-loop/
    # overlong-path all collapse to the SAME pre-existing
    # `MANIFEST_INVALID_REASON_V2` this CLI already used, since this CLI
    # never distinguished those cases before either.
    try:
        raw = validate_external_input_file_v2(path).read_text(encoding="utf-8")
    except ExternalPathIngressError as exc:
        raise PayloadSetCliError(MANIFEST_INVALID_REASON_V2) from exc
    try:
        return ManifestV2.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise PayloadSetCliError(MANIFEST_INVALID_REASON_V2) from exc


def _load_payloads(payloads_dir: Path) -> list[ChunkPayloadV2]:
    # G4B: the "responses directory" ingress shape. Enumeration AND every
    # discovered file's read now go through the authority's directory
    # capability rather than a raw `.glob()` + `.read_text()` pair -- a
    # directory-level failure (missing/wrong-type/unreadable/symlink-loop)
    # gets its own distinct reason code; a per-file failure at read time
    # (TOCTOU deletion, permission change) still lands on the existing
    # `PAYLOAD_INVALID_REASON_V2`.
    try:
        capability = validate_external_input_directory_v2(payloads_dir)
        entries = tuple(
            entry for entry in capability.iter_input_files() if entry.entry_name.endswith(".json")
        )
    except ExternalPathIngressError as exc:
        raise PayloadSetCliError(PAYLOADS_DIR_UNUSABLE_REASON_V2) from exc
    if not entries:
        raise PayloadSetCliError(NO_PAYLOADS_FOUND_REASON_V2)
    payloads: list[ChunkPayloadV2] = []
    for entry in entries:
        try:
            raw = entry.read_text(encoding="utf-8")
        except ExternalPathIngressError as exc:
            raise PayloadSetCliError(PAYLOAD_INVALID_REASON_V2) from exc
        try:
            payloads.append(ChunkPayloadV2.model_validate_json(raw, strict=True))
        except ValidationError as exc:
            raise PayloadSetCliError(PAYLOAD_INVALID_REASON_V2) from exc
    return payloads


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.contract_version != "v2":
        print(f"error: {CONTRACT_VERSION_MISSING_REASON_V2}", file=sys.stderr)
        return 1

    try:
        manifest = _load_manifest(Path(args.manifest))
        payloads = _load_payloads(Path(args.payloads_dir))
        payload_set = emit_payload_set_v2(manifest, payloads)
    except (PayloadSetCliError, PayloadSetBindingError) as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1

    # G4B: this write sat outside every try/except in `main()` -- a
    # permission-denied parent, a full disk, an overlong `--output` path, or
    # a symlink loop while creating `parents=True` intermediate directories
    # each crashed raw, uncaught.
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload_set.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError):
        print(f"error: {OUTPUT_WRITE_FAILED_REASON_V2}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
