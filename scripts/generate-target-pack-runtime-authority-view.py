#!/usr/bin/env python3
"""Emit the deterministic INTERNAL view of the Target Pack runtime authorities.

```
Python registry = AUTHORITY          this JSON = GENERATED VIEW
never             Python authority <-> JSON authority
```

The runtime never reads this file. It exists so tooling and documentation can
consume the two runtime relations WITHOUT parsing Python -- which `#203-D0`
proved three times is not something static analysis can do soundly. A future
documentation compiler reads it at a pinned commit:

    git show <implementation_anchor>:docs/generated/target-pack-runtime-authority.v1.json

and parses closed JSON, permanently retiring argparse AST, constant AST,
assignment/reassignment analysis, and any import or execution of historical
Python.

Only non-derivable facts are persisted. `total`, `locally_evaluable` counts,
and a separate `unvalidated_capabilities` list are all functions of the check
specs below, so they are NOT written here -- a consumer derives them. Writing
them would create a second representation of one fact, which is the defect
class this whole workstream exists to remove.

`--check` proves byte-identity without writing -- the CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.target_pack_runtime_authority_v2 import (  # noqa: E402
    TARGET_PACK_VALIDATE_CHECKS_V2,
    cli_command_names_v2,
)

VIEW_PATH = REPO_ROOT / "docs" / "generated" / "target-pack-runtime-authority.v1.json"

VIEW_FORMAT_ID_V1 = "aiops.agent-review.internal.target-pack-runtime-authority-view.v1"
VIEW_GENERATOR_ID_V1 = "target-pack-runtime-authority-view-v1"


def render_view() -> str:
    payload = {
        "format_id": VIEW_FORMAT_ID_V1,
        "generated": {"generator": VIEW_GENERATOR_ID_V1},
        # Command order carries no runtime meaning -- argparse display order is
        # presentation -- so it is sorted for a stable view.
        "cli_surface": {"commands": sorted(cli_command_names_v2())},
        # Check order DOES carry meaning: it is the canonical report order the
        # finalizer applies, so declaration order is preserved verbatim.
        "validate_check_domain": {
            "checks": [
                {
                    "name": spec.name,
                    "evaluation_class": spec.evaluation_class.value,
                    "unvalidated_reason_code": spec.unvalidated_reason_code,
                }
                for spec in TARGET_PACK_VALIDATE_CHECKS_V2
            ]
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail without writing when the view differs")
    args = parser.parse_args(argv)

    rendered = render_view()

    if args.check:
        if not VIEW_PATH.exists():
            print(f"runtime authority view missing: {VIEW_PATH}", file=sys.stderr)
            return 1
        if VIEW_PATH.read_text(encoding="utf-8") != rendered:
            print(
                f"runtime authority view is stale -- regenerate with this script (no --check): {VIEW_PATH}",
                file=sys.stderr,
            )
            return 1
        print("target-pack runtime authority view is byte-identical to the declared authorities.")
        return 0

    VIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    VIEW_PATH.write_text(rendered, encoding="utf-8")
    print(f"target-pack runtime authority view regenerated ({VIEW_PATH}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
