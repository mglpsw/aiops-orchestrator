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
from app.agent_review.manifest_v2 import ManifestV2  # noqa: E402
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2  # noqa: E402
from app.agent_review.payload_set_v2 import PayloadSetBindingError  # noqa: E402

CONTRACT_VERSION_MISSING_REASON_V2 = "contract_version_required"
MANIFEST_INVALID_REASON_V2 = "manifest_invalid"
PAYLOAD_INVALID_REASON_V2 = "payload_invalid"
NO_PAYLOADS_FOUND_REASON_V2 = "no_payloads_found"


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
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PayloadSetCliError(MANIFEST_INVALID_REASON_V2) from exc
    try:
        return ManifestV2.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise PayloadSetCliError(MANIFEST_INVALID_REASON_V2) from exc


def _load_payloads(payloads_dir: Path) -> list[ChunkPayloadV2]:
    payload_paths = sorted(payloads_dir.glob("*.json"))
    if not payload_paths:
        raise PayloadSetCliError(NO_PAYLOADS_FOUND_REASON_V2)
    payloads: list[ChunkPayloadV2] = []
    for payload_path in payload_paths:
        try:
            raw = payload_path.read_text(encoding="utf-8")
            payloads.append(ChunkPayloadV2.model_validate_json(raw, strict=True))
        except (OSError, ValidationError) as exc:
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

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload_set.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
