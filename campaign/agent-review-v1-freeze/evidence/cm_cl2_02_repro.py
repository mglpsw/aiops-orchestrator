"""V1-C0 evidence: CM-CL2-02 flattening reproduction (read-only).

Feeds the exact AgentEscala contract documents, identified by git blob SHA, through
v1's real flatteners. Inputs are obtained outside this script, e.g.
    gh api repos/mglpsw/AgentEscala/git/blobs/<sha> --jq .content | base64 -d > <file>
and each file's git blob identity is verified before use.

Usage (repo root, project venv):
    PYTHONDONTWRITEBYTECODE=1 python -B campaign/agent-review-v1-freeze/evidence/cm_cl2_02_repro.py \
        <domain-contracts.yaml> <review-packs.yaml>

Scope: reproduces only that both documents flatten to [] (the flattening step). That the
planner then emits `contracts_context_not_relevant:<chunk>` follows from
payload_cost_model.py:435-436 by code reading and is NOT exercised here.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path.cwd()))
from app.agent_review.payload_cost_model import _flatten_contract_rules, _flatten_review_packs  # noqa: E402

EXPECTED = {
    "domain-contracts": "e0ca56844cceaba1afac325856e305ca1342257e",
    "review-packs": "16ae9a5d1d494f1128f3ed9b50a80d84e5797eec",
}


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main() -> int:
    contracts_path, packs_path = map(Path, sys.argv[1:3])
    for label, path in (("domain-contracts", contracts_path), ("review-packs", packs_path)):
        actual = _git_blob_sha(path.read_bytes())
        if actual != EXPECTED[label]:
            print(f"{label}: blob {actual} != expected {EXPECTED[label]}; refusing")
            return 1
    contracts = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
    packs = yaml.safe_load(packs_path.read_text(encoding="utf-8"))
    print(f"domain-contracts blob={EXPECTED['domain-contracts']} top_level_rules={type(contracts.get('rules')).__name__} flattened={len(_flatten_contract_rules(contracts))}")
    print(f"review-packs blob={EXPECTED['review-packs']} packs_type={type(packs.get('packs')).__name__} flattened={len(_flatten_review_packs(packs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
