#!/usr/bin/env python3
"""Verify the pinned CAEM 3.0 F0 identity against a local artifact copy.

Read-only: never writes, never touches the network, never reads or verifies
GitHub state. Exits non-zero on any drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.caem_consumer.f0 import (  # noqa: E402
    load_caem_f0_interface,
    load_caem_f0_pin,
    scan_active_identity_headers,
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", default="config/caem/caem-3.0-f0.pin.json", help="path to the consumer pin")
    parser.add_argument("--interface-root", help="path to a local mglpsw/caem checkout")
    parser.add_argument("--transport-zip", help="path to a local caem-3.0-f0-interface.zip")
    parser.add_argument("--git-root", help="optional git working tree to verify blob-OID projection against")
    parser.add_argument(
        "--check",
        action="store_true",
        help="also scan the five active generated-view headers (AGENTS.md, CLAUDE.md, "
        "CAEM_CORE.md, PROJECT_OVERLAY.md, CURRENT_CHECKPOINT.md) for stale CAEM identity "
        "strings — not a repository-wide scan; historical documents may legitimately "
        "mention these strings",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    pin_result = load_caem_f0_pin(args.pin)
    if not pin_result.ok:
        for error in pin_result.errors:
            print(f"PIN {error.reason_code}: {error.message}", file=sys.stderr)
        return 1
    print(f"pin: ok (carrier {pin_result.pin.carrier_sha})")

    if args.interface_root or args.transport_zip:
        iface_result = load_caem_f0_interface(
            pin_result.pin,
            interface_root=args.interface_root,
            transport_zip=args.transport_zip,
            git_root=args.git_root,
        )
        if not iface_result.ok:
            for error in iface_result.errors:
                print(f"INTERFACE {error.reason_code}: {error.message}", file=sys.stderr)
            return 1
        print(f"interface: ok ({len(iface_result.contracts)} contracts)")

    if args.check:
        hits = scan_active_identity_headers(REPO_ROOT)
        if hits:
            for path, needle in hits:
                print(f"STALE_IDENTITY: {path} contains {needle[:16]}...", file=sys.stderr)
            return 1
        print("active generated-view headers: ok (single consistent identity)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
