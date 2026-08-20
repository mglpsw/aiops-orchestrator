#!/usr/bin/env python3
"""Regenerate `docs/generated/target-pack-current-state.json` and every
target-pack ANCHOR-STATE Markdown block from
`config/agent-review/target-pack-current-inputs.json` plus the anchor's own
static authorities (`#203-D0`, post-`STOP_REDESIGN_2`).

`--check` proves byte-identity without writing -- the CI gate.

*(The physical filenames still say `current`; that is recorded compatibility
debt from the structural replacement, scheduled for a separate path-rename
change. Every machine-readable identifier emitted here says `anchor`.)*

This script performs NO ref discovery and has no remote dependency: it never
resolves `origin/master` or any other remote-tracking ref, so a checkout whose
remote is renamed, absent, or represented only by local branches compiles
identically.
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
    TargetPackAnchorStateV1,
    TargetPackAnchorStateError,
    compile_anchor_state,
    extract_normative_surface,
    git_commit_committed_at,
    git_commit_exists,
    git_commit_message,
    git_is_ancestor,
    load_anchor_inputs,
    read_anchor_blob,
    render_anchor_state_json,
    verify_anchor_coherence,
)

INPUTS_PATH = REPO_ROOT / "config" / "agent-review" / "target-pack-current-inputs.json"
ANCHOR_STATE_JSON_PATH = REPO_ROOT / "docs" / "generated" / "target-pack-current-state.json"
SPEC_PATH = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md"
README_PATH = REPO_ROOT / "README.md"
PROJECT_STATUS_PATH = REPO_ROOT / "docs" / "PROJECT_STATUS.md"
ARCHITECTURE_PATH = REPO_ROOT / "docs" / "ARCHITECTURE.md"
CHECKPOINT_PATH = REPO_ROOT / "docs" / "engineering" / "CURRENT_CHECKPOINT.md"
TARGET_PACK_DOC_PATH = REPO_ROOT / "docs" / "AGENT_REVIEW_V2_TARGET_PACK.md"

_SOURCE_INPUTS_REL = "config/agent-review/target-pack-current-inputs.json"
_SPEC_REL = "docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md"


# --- Renderers -- one deterministic text per renderer id, reused verbatim
# across every slot that names it. Each renderer's exact output is a
# load-bearing golden test: the claim strength lives in these strings. ------


def _render_status(state: TargetPackAnchorStateV1) -> str:
    exposed = ", ".join(f"`{n}`" for n in sorted(state.exposed_at_anchor))
    not_exposed = ", ".join(f"`{n}`" for n in sorted(state.declared_not_exposed_at_anchor))
    return (
        f"Exposed by the target-pack CLI at implementation anchor "
        f"`{state.implementation_anchor}`: {exposed}. Declared in the normative surface but not "
        f"exposed at that anchor: {not_exposed}."
    )


def _render_temporal(state: TargetPackAnchorStateV1) -> str:
    return (
        f"Implementation anchor: `{state.implementation_anchor}` "
        f"(committed {state.anchor_committed_at.isoformat()}). "
        f"Anchor-state reconciled at: {state.reconciled_at.isoformat()}."
    )


def _render_lifecycle_prose(state: TargetPackAnchorStateV1) -> str:
    exposed = ", ".join(f"`{n}`" for n in sorted(state.exposed_at_anchor))
    return (
        f"{exposed} are exposed by the target-pack CLI at implementation anchor "
        f"`{state.implementation_anchor}`.\n"
        f"The remaining declared subcommands are not exposed at that anchor."
    )


def _render_inventory(state: TargetPackAnchorStateV1) -> str:
    inv = state.validate_inventory
    names = ", ".join(f"`{n}`" for n in sorted(inv.unvalidated_capabilities))
    return (
        f"**Check inventory at implementation anchor `{state.implementation_anchor}`:** "
        f"{inv.total} total dimensions, {inv.locally_evaluable} locally evaluable when applicable, "
        f"{len(inv.unvalidated_capabilities)} reported `unavailable` because this validate "
        f"implementation cannot establish them from the target alone: {names}."
    )


def _render_evidence(state: TargetPackAnchorStateV1) -> str:
    if not state.commit_message_evidence:
        return "No commit-message evidence records."
    return "\n".join(
        f"Commit-message evidence at `{e.commit_sha}` records: "
        f"full suite {e.suite_passed} passed, {e.suite_skipped} skipped."
        for e in state.commit_message_evidence
    )


RENDERERS: dict[str, Callable[[TargetPackAnchorStateV1], str]] = {
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


SLOT_NAMESPACE_PREFIX = "target-pack-anchor."

# Retired by `STOP_REDESIGN_2`. Its population across tracked Markdown must be
# exactly zero: once SLOT_NAMESPACE_PREFIX moved, the global scan below stops
# recognising the old prefix entirely, so a residual old marker sitting beside
# a correct new one would otherwise be invisible. This is the retirement rule
# for ONE exact typed namespace -- not semantic inference from vocabulary.
RETIRED_SLOT_NAMESPACE_PREFIX = "target-pack-current."

VIEW_SLOTS: tuple[ViewSlot, ...] = (
    ViewSlot("target-pack-anchor.readme.status", README_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-anchor.project-status.status", PROJECT_STATUS_PATH, "target-pack-status"),
    ViewSlot("target-pack-anchor.project-status.temporal", PROJECT_STATUS_PATH, "target-pack-temporal"),
    ViewSlot("target-pack-anchor.architecture.status", ARCHITECTURE_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-anchor.checkpoint.temporal", CHECKPOINT_PATH, "target-pack-temporal", inline=True),
    ViewSlot("target-pack-anchor.checkpoint.status", CHECKPOINT_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-anchor.target-pack-doc.status", TARGET_PACK_DOC_PATH, "target-pack-status", inline=True),
    ViewSlot("target-pack-anchor.target-pack-doc.inventory", TARGET_PACK_DOC_PATH, "target-pack-inventory"),
    ViewSlot("target-pack-anchor.target-pack-doc.evidence", TARGET_PACK_DOC_PATH, "target-pack-evidence"),
    ViewSlot("target-pack-anchor.spec.lifecycle-prose", SPEC_PATH, "target-pack-lifecycle-prose"),
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
    """Every `*.md` file Git tracks -- NOT merely the paths named in
    `VIEW_SLOTS`. A file that gained an unregistered marker (or a copy of a
    registered one) but is never opened by the per-slot rendering loop would
    otherwise be invisible to `--check`, defeating the closed registry."""

    proc = subprocess.run(
        ["git", "ls-files", "--", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / line for line in proc.stdout.splitlines() if line]


def _verify_global_slot_registry(rendered_by_path: dict[Path, str]) -> list[str]:
    """Scans EVERY tracked Markdown file and cross-checks both namespaces:
    an unknown `target-pack-anchor.*` marker anywhere, a known slot_id in a
    file other than its one registered path, or any surviving marker in the
    RETIRED `target-pack-current.*` namespace."""

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
        for slot_id in sorted(set(marker_re.findall(text))):
            if slot_id.startswith(RETIRED_SLOT_NAMESPACE_PREFIX):
                problems.append(
                    f"{md_path}: retired slot namespace marker {slot_id!r} -- "
                    f"{RETIRED_SLOT_NAMESPACE_PREFIX}* population must be zero"
                )
                continue
            if not slot_id.startswith(SLOT_NAMESPACE_PREFIX):
                continue
            if slot_id not in registered_ids:
                problems.append(f"{md_path}: unregistered marker {slot_id!r}")
            elif slot_by_id[slot_id].path != md_path:
                problems.append(
                    f"{md_path}: marker {slot_id!r} is registered to {slot_by_id[slot_id].path}, not here"
                )
    return problems


def _load_state() -> TargetPackAnchorStateV1:
    inputs = load_anchor_inputs(INPUTS_PATH, commit_exists=lambda sha: git_commit_exists(REPO_ROOT, sha))

    verify_anchor_coherence(
        anchor=inputs.implementation_anchor,
        commit_exists=lambda sha: git_commit_exists(REPO_ROOT, sha),
        is_ancestor=lambda a, d: git_is_ancestor(REPO_ROOT, a, d),
    )

    normative_surface = extract_normative_surface(SPEC_PATH.read_text(encoding="utf-8"))

    return compile_anchor_state(
        inputs=inputs,
        normative_surface=normative_surface,
        normative_surface_source_path=_SPEC_REL,
        read_blob=lambda anchor, path: read_anchor_blob(REPO_ROOT, anchor, path),
        committed_at=lambda anchor: git_commit_committed_at(REPO_ROOT, anchor),
        commit_message=lambda sha: git_commit_message(REPO_ROOT, sha),
        is_ancestor=lambda a, d: git_is_ancestor(REPO_ROOT, a, d),
    )


def _render_all(state: TargetPackAnchorStateV1) -> tuple[str, dict[Path, str]]:
    anchor_state_json = render_anchor_state_json(state, source_inputs_path=_SOURCE_INPUTS_REL)

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

    return anchor_state_json, by_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail without writing when generated output differs")
    args = parser.parse_args(argv)

    try:
        state = _load_state()
        anchor_state_json, rendered_docs = _render_all(state)
    except (TargetPackAnchorStateError, GeneratorError) as exc:
        print(f"target-pack anchor-state compile/render failed: {exc}", file=sys.stderr)
        return 2

    if args.check:
        drift: list[str] = []
        if not ANCHOR_STATE_JSON_PATH.exists() or ANCHOR_STATE_JSON_PATH.read_text(encoding="utf-8") != anchor_state_json:
            drift.append(str(ANCHOR_STATE_JSON_PATH))
        for path, text in rendered_docs.items():
            if path.read_text(encoding="utf-8") != text:
                drift.append(str(path))
        if drift:
            print("target-pack anchor-state view is stale -- regenerate with this script (no --check):", file=sys.stderr)
            for d in drift:
                print(f"  {d}", file=sys.stderr)
            return 1
        print("target-pack anchor-state view is byte-identical to the compiled projection.")
        return 0

    ANCHOR_STATE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANCHOR_STATE_JSON_PATH.write_text(anchor_state_json, encoding="utf-8")
    for path, text in rendered_docs.items():
        path.write_text(text, encoding="utf-8")
    print(f"target-pack anchor-state view regenerated ({1 + len(rendered_docs)} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
