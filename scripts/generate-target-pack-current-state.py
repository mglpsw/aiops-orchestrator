#!/usr/bin/env python3
"""Regenerate `docs/generated/target-pack-current-state.json` and every
target-pack CURRENT Markdown block from
`config/agent-review/target-pack-current-inputs.json` plus the anchor's own
static authorities (`#203-D0` successor).

`--check` proves byte-identity without writing -- the CI gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.target_pack_current_state_v1 import (  # noqa: E402
    AUTHORITY_BEARING_PATHS_V1,
    CANONICAL_REF_V1,
    TargetPackCurrentStateV1,
    TargetPackCurrentStateError,
    compile_current_state,
    extract_declared_surface,
    git_commit_committed_at,
    git_commit_exists,
    git_commit_message,
    git_is_ancestor,
    git_ref_exists,
    load_current_inputs,
    read_anchor_blob,
    render_compiled_json,
    verify_anchor_freshness,
)

INPUTS_PATH = REPO_ROOT / "config" / "agent-review" / "target-pack-current-inputs.json"
COMPILED_JSON_PATH = REPO_ROOT / "docs" / "generated" / "target-pack-current-state.json"
SPEC_PATH = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md"
README_PATH = REPO_ROOT / "README.md"
PROJECT_STATUS_PATH = REPO_ROOT / "docs" / "PROJECT_STATUS.md"
ARCHITECTURE_PATH = REPO_ROOT / "docs" / "ARCHITECTURE.md"
CHECKPOINT_PATH = REPO_ROOT / "docs" / "engineering" / "CURRENT_CHECKPOINT.md"
TARGET_PACK_DOC_PATH = REPO_ROOT / "docs" / "AGENT_REVIEW_V2_TARGET_PACK.md"

_SOURCE_INPUTS_REL = "config/agent-review/target-pack-current-inputs.json"


# --- Renderers -- one deterministic text per renderer id, reused verbatim
# across every slot that names it. -----------------------------------------


def _render_status(state: TargetPackCurrentStateV1) -> str:
    canonical = ", ".join(f"`{n}`" for n in sorted(state.canonical))
    deferred = ", ".join(f"`{n}`" for n in sorted(state.deferred))
    return f"Canonical on `master`: {canonical}. Deferred: {deferred}."


def _render_temporal(state: TargetPackCurrentStateV1) -> str:
    return (
        f"Implementation anchor: `{state.implementation_anchor}` "
        f"(committed {state.anchor_committed_at.isoformat()}). "
        f"Reconciled at: {state.reconciled_at.isoformat()}."
    )


def _render_lifecycle_prose(state: TargetPackCurrentStateV1) -> str:
    canonical = ", ".join(f"`{n}`" for n in sorted(state.canonical))
    deferred = ", ".join(f"`{n}`" for n in sorted(state.deferred))
    return (
        f"{canonical} are implemented and **canonical on `master`**.\n"
        f"{deferred} remain specified here and deferred (§14)."
    )


def _render_inventory(state: TargetPackCurrentStateV1) -> str:
    names = ", ".join(f"`{n}`" for n in sorted(state.validate_permanently_unavailable))
    return (
        f"**Check inventory (derived from the anchor, `{state.implementation_anchor}`):** "
        f"{state.validate_total} total dimensions, {state.validate_locally_evaluable} locally evaluable "
        f"when applicable, {len(state.validate_permanently_unavailable)} permanently disclosed "
        f"`unavailable`: {names}."
    )


def _render_evidence(state: TargetPackCurrentStateV1) -> str:
    if not state.historical_evidence:
        return "No historical evidence records."
    lines = []
    for e in state.historical_evidence:
        lines.append(
            f"C2 canonical qualification at `{e.canonical_sha}`: full suite {e.suite_passed} passed, "
            f"{e.suite_skipped} skipped; source: canonical commit message "
            f"({e.evidence_ref_kind}@`{e.evidence_ref_sha}`)."
        )
    return "\n".join(lines)


RENDERERS: dict[str, Callable[[TargetPackCurrentStateV1], str]] = {
    "target-pack-status": _render_status,
    "target-pack-temporal": _render_temporal,
    "target-pack-lifecycle-prose": _render_lifecycle_prose,
    "target-pack-inventory": _render_inventory,
    "target-pack-evidence": _render_evidence,
}


# --- Closed slot registry --------------------------------------------------


@dataclass(frozen=True)
class ViewSlot:
    slot_id: str
    path: Path
    renderer: str
    inline: bool = False  # True: no surrounding newlines (e.g. a table cell)


SLOT_NAMESPACE_PREFIX = "target-pack-current."

VIEW_SLOTS: tuple[ViewSlot, ...] = (
    ViewSlot("target-pack-current.readme.status", README_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-current.project-status.status", PROJECT_STATUS_PATH, "target-pack-status"),
    ViewSlot("target-pack-current.project-status.temporal", PROJECT_STATUS_PATH, "target-pack-temporal"),
    ViewSlot("target-pack-current.architecture.status", ARCHITECTURE_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-current.checkpoint.temporal", CHECKPOINT_PATH, "target-pack-temporal", inline=True),
    ViewSlot("target-pack-current.checkpoint.status", CHECKPOINT_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-current.target-pack-doc.status", TARGET_PACK_DOC_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-current.target-pack-doc.inventory", TARGET_PACK_DOC_PATH, "target-pack-inventory"),
    ViewSlot("target-pack-current.target-pack-doc.evidence", TARGET_PACK_DOC_PATH, "target-pack-evidence"),
    ViewSlot("target-pack-current.spec.lifecycle-prose", SPEC_PATH, "target-pack-lifecycle-prose"),
)

assert len({s.slot_id for s in VIEW_SLOTS}) == len(VIEW_SLOTS), "duplicate slot_id in VIEW_SLOTS"
for _s in VIEW_SLOTS:
    assert _s.slot_id.startswith(SLOT_NAMESPACE_PREFIX), f"slot_id {_s.slot_id!r} outside the namespace"
    assert _s.renderer in RENDERERS, f"slot {_s.slot_id!r} references unknown renderer {_s.renderer!r}"


class GeneratorError(Exception):
    pass


def _begin_marker(slot_id: str) -> str:
    return f"<!-- BEGIN GENERATED: {slot_id} -->"


def _end_marker(slot_id: str) -> str:
    return f"<!-- END GENERATED: {slot_id} -->"


def _replace_slot(text: str, slot_id: str, new_content: str, *, inline: bool) -> str:
    begin, end = _begin_marker(slot_id), _end_marker(slot_id)
    if text.count(begin) != 1:
        raise GeneratorError(f"{slot_id}: expected exactly one {begin!r}, found {text.count(begin)}")
    if text.count(end) != 1:
        raise GeneratorError(f"{slot_id}: expected exactly one {end!r}, found {text.count(end)}")
    begin_idx = text.index(begin)
    end_idx = text.index(end)
    if end_idx < begin_idx:
        raise GeneratorError(f"{slot_id}: END marker precedes BEGIN marker")
    before = text[: begin_idx + len(begin)]
    after = text[end_idx:]
    if inline:
        if "\n" in new_content:
            raise GeneratorError(f"{slot_id}: inline slot content must not contain a newline")
        return f"{before}{new_content}{after}"
    return f"{before}\n{new_content}\n{after}"


def _find_unregistered_markers(text: str, path: Path, registered_ids: set[str]) -> list[str]:
    offenders = []
    for m in re.finditer(r"<!-- (?:BEGIN|END) GENERATED: (\S+) -->", text):
        slot_id = m.group(1)
        if slot_id.startswith(SLOT_NAMESPACE_PREFIX) and slot_id not in registered_ids:
            offenders.append(f"{path}: unregistered marker {slot_id!r}")
    return offenders


def _tracked_markdown_files() -> list[Path]:
    """Every `*.md` file Git tracks in this repository -- NOT merely the
    paths already named in `VIEW_SLOTS`. A file that gained an unregistered
    `target-pack-current.*` marker (or a copy of a REGISTERED one) but was
    never opened by the per-slot rendering loop would otherwise be invisible
    to `--check` entirely, defeating the closed-registry claim."""

    proc = subprocess.run(
        ["git", "ls-files", "--", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / line for line in proc.stdout.splitlines() if line]


def _verify_global_slot_registry(rendered_by_path: dict[Path, str]) -> list[str]:
    """Scans EVERY tracked Markdown file in the repository, not only the
    ones `VIEW_SLOTS` already names, and cross-checks the namespace against
    the registry: an unknown `target-pack-current.*` marker anywhere is a
    violation, and so is a KNOWN slot_id appearing in a file other than its
    one registered path (an unowned duplicate that `--check` on the
    registered path alone would never see, since that path's own marker
    count stays exactly one)."""

    registered_ids = {s.slot_id for s in VIEW_SLOTS}
    slot_by_id = {s.slot_id: s for s in VIEW_SLOTS}
    marker_re = re.compile(r"<!-- (?:BEGIN|END) GENERATED: (\S+) -->")
    problems: list[str] = []

    for md_path in _tracked_markdown_files():
        text = rendered_by_path.get(md_path)
        if text is None:
            if not md_path.exists():
                continue
            text = md_path.read_text(encoding="utf-8")
        for slot_id in set(marker_re.findall(text)):
            if not slot_id.startswith(SLOT_NAMESPACE_PREFIX):
                continue
            if slot_id not in registered_ids:
                problems.append(f"{md_path}: unregistered marker {slot_id!r}")
            elif slot_by_id[slot_id].path != md_path:
                problems.append(
                    f"{md_path}: marker {slot_id!r} is registered to {slot_by_id[slot_id].path}, not here"
                )
    return problems


def _load_state() -> TargetPackCurrentStateV1:
    inputs = load_current_inputs(INPUTS_PATH, commit_exists=lambda sha: git_commit_exists(REPO_ROOT, sha))
    declared_surface = extract_declared_surface(SPEC_PATH.read_text(encoding="utf-8"))

    verify_anchor_freshness(
        anchor=inputs.implementation_anchor,
        canonical_ref=CANONICAL_REF_V1,
        canonical_ref_exists=lambda ref: git_ref_exists(REPO_ROOT, ref),
        is_ancestor=lambda ancestor, ref: git_is_ancestor(REPO_ROOT, ancestor, ref),
        read_blob_at_ref=lambda ref, path: read_anchor_blob(REPO_ROOT, ref, path),
        authority_paths=AUTHORITY_BEARING_PATHS_V1,
    )

    return compile_current_state(
        inputs=inputs,
        declared_surface=declared_surface,
        read_blob=lambda anchor, path: read_anchor_blob(REPO_ROOT, anchor, path),
        committed_at=lambda anchor: git_commit_committed_at(REPO_ROOT, anchor),
        commit_message=lambda sha: git_commit_message(REPO_ROOT, sha),
    )


def _render_all(state: TargetPackCurrentStateV1) -> tuple[str, dict[Path, str]]:
    compiled_json = render_compiled_json(state, source_inputs_path=_SOURCE_INPUTS_REL)

    by_path: dict[Path, str] = {}
    registered_ids = {s.slot_id for s in VIEW_SLOTS}
    for path in {s.path for s in VIEW_SLOTS}:
        by_path[path] = path.read_text(encoding="utf-8")

    for slot in VIEW_SLOTS:
        content = RENDERERS[slot.renderer](state)
        by_path[slot.path] = _replace_slot(by_path[slot.path], slot.slot_id, content, inline=slot.inline)

    for path, text in by_path.items():
        offenders = _find_unregistered_markers(text, path, registered_ids)
        if offenders:
            raise GeneratorError("unregistered marker(s) found:\n" + "\n".join(offenders))

    global_problems = _verify_global_slot_registry(by_path)
    if global_problems:
        raise GeneratorError("global slot registry violation(s):\n" + "\n".join(global_problems))

    return compiled_json, by_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail without writing when generated output differs")
    args = parser.parse_args(argv)

    try:
        state = _load_state()
        compiled_json, rendered_docs = _render_all(state)
    except (TargetPackCurrentStateError, GeneratorError) as exc:
        print(f"target-pack CURRENT compile/render failed: {exc}", file=sys.stderr)
        return 2

    if args.check:
        drift: list[str] = []
        if not COMPILED_JSON_PATH.exists() or COMPILED_JSON_PATH.read_text(encoding="utf-8") != compiled_json:
            drift.append(str(COMPILED_JSON_PATH))
        for path, text in rendered_docs.items():
            if path.read_text(encoding="utf-8") != text:
                drift.append(str(path))
        if drift:
            print("target-pack CURRENT view is stale -- regenerate with this script (no --check):", file=sys.stderr)
            for d in drift:
                print(f"  {d}", file=sys.stderr)
            return 1
        print("target-pack CURRENT view is byte-identical to the compiled state.")
        return 0

    COMPILED_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPILED_JSON_PATH.write_text(compiled_json, encoding="utf-8")
    for path, text in rendered_docs.items():
        path.write_text(text, encoding="utf-8")
    print(f"target-pack CURRENT view regenerated ({1 + len(rendered_docs)} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
