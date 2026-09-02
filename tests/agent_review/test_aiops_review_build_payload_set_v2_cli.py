from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
from app.agent_review.payload_set_v2 import PayloadSetV2
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-build-payload-set-v2.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 132,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _hunk(path: str) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=0,
        old_start=1,
        old_end=5,
        new_start=1,
        new_end=5,
        diff_sha256=hashlib.sha256(path.encode()).hexdigest(),
        diff_chars=40,
        must_review=True,
    )


def _build_manifest(paths: list[str]) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        [_hunk(p) for p in paths], semantic_group="primary_backend_logic", max_lines_per_chunk=5, max_chunks=10
    )
    assert outcome.state == "planned"
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": paths,
        "must_review_files": paths,
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _write_fixtures(tmp_path: Path, *, paths: list[str] = ("app/a.py", "app/b.py")) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest(list(paths))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")

    payloads_dir = tmp_path / "payloads"
    payloads_dir.mkdir()
    for built in build_chunk_payloads_v2(manifest):
        (payloads_dir / f"{built.chunk_id}.json").write_text(
            json.dumps(built.payload.model_dump(mode="json")), encoding="utf-8"
        )
    return manifest_path, payloads_dir


def test_cli_emits_a_valid_payload_set(tmp_path: Path) -> None:
    manifest_path, payloads_dir = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version",
            "v2",
            "--manifest",
            str(manifest_path),
            "--payloads-dir",
            str(payloads_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    payload_set = PayloadSetV2.model_validate(doc)  # must construct and self-validate cleanly
    assert len(payload_set.payloads) == 2


def test_cli_refuses_without_contract_version_v2(tmp_path: Path) -> None:
    manifest_path, payloads_dir = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version",
            "v1",
            "--manifest",
            str(manifest_path),
            "--payloads-dir",
            str(payloads_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()


def test_cli_fails_closed_on_a_manifest_payload_mismatch(tmp_path: Path) -> None:
    manifest_path, _ = _write_fixtures(tmp_path)
    other_manifest_path, other_payloads_dir = _write_fixtures(tmp_path / "other", paths=["app/c.py", "app/d.py"])
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version",
            "v2",
            "--manifest",
            str(manifest_path),
            "--payloads-dir",
            str(other_payloads_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()


def test_cli_fails_closed_on_an_empty_payloads_directory(tmp_path: Path) -> None:
    manifest_path, _ = _write_fixtures(tmp_path)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version",
            "v2",
            "--manifest",
            str(manifest_path),
            "--payloads-dir",
            str(empty_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# G4B (#200-G4B): `--manifest` and `--payloads-dir` (the "responses
# directory" ingress shape for this CLI) now read through the centralized
# external-path ingress authority instead of a bare `Path.read_text()`/
# `.glob()`. RED/GREEN witnesses that a symlink loop is a typed refusal,
# never a raw traceback.
# ---------------------------------------------------------------------------


def test_cli_refuses_a_manifest_symlink_loop_instead_of_crashing(tmp_path: Path) -> None:
    """RED against the unfixed shape: `_load_manifest`'s own
    `path.read_text()` had no guard at all for a symlink loop (pathlib's own
    `RuntimeError`)."""

    _, payloads_dir = _write_fixtures(tmp_path)
    a = tmp_path / "loop_a"
    b = tmp_path / "loop_b"
    a.symlink_to("loop_b")
    b.symlink_to("loop_a")
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(a),
            "--payloads-dir", str(payloads_dir),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "manifest_invalid" in result.stderr
    assert not output_path.exists()


def test_cli_refuses_a_payloads_dir_symlink_loop_instead_of_crashing(tmp_path: Path) -> None:
    """RED against the unfixed shape: `_load_payloads`'s own
    `payloads_dir.glob("*.json")` was completely unguarded -- a symlink
    loop AT `--payloads-dir` itself raised a raw `RuntimeError` before a
    single payload was ever read."""

    manifest_path, _ = _write_fixtures(tmp_path)
    a = tmp_path / "loop_dir_a"
    b = tmp_path / "loop_dir_b"
    a.symlink_to("loop_dir_b")
    b.symlink_to("loop_dir_a")
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(manifest_path),
            "--payloads-dir", str(a),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "payloads_dir_unusable" in result.stderr
    assert not output_path.exists()


def test_cli_refuses_a_payloads_dir_pointed_at_a_file_instead_of_crashing(tmp_path: Path) -> None:
    """RED against the unfixed shape: `payloads_dir.glob("*.json")` on a
    FILE (not a directory) raised a raw `NotADirectoryError` (an `OSError`
    subclass the old code never caught at all)."""

    manifest_path, _ = _write_fixtures(tmp_path)
    not_a_dir = tmp_path / "payloads_is_a_file"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(manifest_path),
            "--payloads-dir", str(not_a_dir),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "payloads_dir_unusable" in result.stderr
    assert not output_path.exists()


def test_cli_refuses_a_write_failure_at_output_instead_of_crashing(tmp_path: Path) -> None:
    """RED against the unfixed shape: the final `--output` write sat
    outside every try/except in `main()`."""

    manifest_path, payloads_dir = _write_fixtures(tmp_path)
    blocking_file = tmp_path / "blocking"
    blocking_file.write_text("not a directory", encoding="utf-8")
    output_path = blocking_file / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(manifest_path),
            "--payloads-dir", str(payloads_dir),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "output_write_failed" in result.stderr


# ---------------------------------------------------------------------------
# Codex P2 (post-G4B, external review of the merged tree): the pre-G4B code
# filtered directory entries by `payloads_dir.glob("*.json")` -- filtering
# on the ENTRY's own (caller-visible) name. The G4B `iter_input_files()`
# migration switched `_load_payloads` to filter on
# `entry.resolved_path.name` instead -- the SYMLINK TARGET's name after
# resolution. `payload.json` and `alias.txt -> payload.json` can now both
# resolve to appearing as `payload.json` (duplicate/collision); conversely
# `alias.json -> payload.txt` stops being recognized as a `.json` entry
# even though the caller-visible name has that extension. Enumeration
# should decide "is this a .json entry" from the entry's own presented
# name; resolution should still happen for the actual read/containment
# check (unchanged). Black-box, full-CLI witnesses -- no reach into CLI
# internals -- since what matters is observable enumeration behavior.
# ---------------------------------------------------------------------------


def test_cli_does_not_double_count_a_non_json_alias_of_a_json_payload(tmp_path: Path) -> None:
    """A single-chunk manifest plus a payloads dir containing the one real
    `<chunk_id>.json` payload AND a non-`.json`-named symlink alias to that
    SAME file must still emit exactly one payload -- the alias must not be
    picked up a second time just because it resolves to a `.json` target."""

    manifest_path, payloads_dir = _write_fixtures(tmp_path, paths=["app/a.py"])
    real_files = sorted(payloads_dir.glob("*.json"))
    assert len(real_files) == 1
    alias = payloads_dir / "alias.txt"
    alias.symlink_to(real_files[0])
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(manifest_path),
            "--payloads-dir", str(payloads_dir),
            "--output", str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    payload_set = PayloadSetV2.model_validate(doc)
    assert len(payload_set.payloads) == 1


def test_cli_recognizes_a_json_named_alias_of_a_non_json_file(tmp_path: Path) -> None:
    """A payloads dir with the real `<chunk_id>.json` payload accessible
    ONLY through a `.json`-named symlink pointing at a differently-named
    real file must still be picked up: enumeration goes by the entry's own
    caller-visible name, not the resolved target's name."""

    manifest_path, payloads_dir = _write_fixtures(tmp_path, paths=["app/a.py"])
    real_files = sorted(payloads_dir.glob("*.json"))
    assert len(real_files) == 1
    real = real_files[0]
    renamed = payloads_dir / "payload.data"
    real.rename(renamed)
    alias = payloads_dir / real.name
    alias.symlink_to(renamed)
    output_path = tmp_path / "out" / "payload-set.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--manifest", str(manifest_path),
            "--payloads-dir", str(payloads_dir),
            "--output", str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    payload_set = PayloadSetV2.model_validate(doc)
    assert len(payload_set.payloads) == 1
