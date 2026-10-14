"""AR-V1-C2 source boundary (#375): unsafe or unreadable target files fail closed and never read as absent.

Property:  GenuinelyAbsentSource != PresentButInvalidOrUnsafeSource

    real file -> canonical loader -> build_intake -> SemanticChunkPlan -> payloads -> synthesis -> QualityGate

Every control starts from real files (synthetic outside-root marker only; no real host file is ever read) and ends at
the shipped CLI chain. A positive control sits next to every refusal so a universal refusal cannot pass.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent_review import artifact_loader
from app.agent_review.artifact_loader import load_declared_artifacts
from app.agent_review.redaction import RedactionState
from app.agent_review.repo_profile import load_repo_profile
from app.agent_review.schemas import ArtifactDeclaration
from app.agent_review.source_boundary import (
    SOURCE_STATE_ABSENT,
    SOURCE_STATE_OVERSIZE,
    SOURCE_STATE_PRESENT,
    SOURCE_STATE_UNSAFE,
    STRUCTURE_CYCLE,
    STRUCTURE_DEPTH,
    STRUCTURE_NODES,
    acquire_source,
    scan_structure,
)
from tests.agent_review.test_v1_method_guidance_carrier import (  # noqa: F401  (autouse offline-env fixture)
    DOMAINS,
    _intake_cli,
    _offline_dev_environment,
    canonical_run,
    cli_chain,
    make_target,
    read_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN = DOMAINS["scheduling"]
MARKER = "SYNTHETIC_OUTSIDE_ROOT_MARKER"
VALID_CONTRACTS = {"c1": {"description": "d1", "paths": ["backend/api/*"], "rules": ["r1"]}}
VALID_PACKS = {"packs": {"p1": {"domain_contract": "c1"}}}
SOURCE_FILE = {"domain_contracts": "domain-contracts.yaml", "review_packs": "review-packs.yaml"}
CRITICAL = {
    "domain_contracts": "invalid_source_contract:MALFORMED_SHAPE",
    "review_packs": "invalid_source_review_packs:MALFORMED_SHAPE",
}
OUTSIDE_BODY = {
    "domain_contracts": f"rules:\n  - {MARKER}\n",
    "review_packs": f"packs:\n  p1:\n    domain_contract: {MARKER}\n",
}


def _target(tmp_path: Path, name: str, kind: str | None = None) -> Any:
    return make_target(
        tmp_path,
        DOMAIN,
        name=name,
        method_guidance=None,
        contracts=VALID_CONTRACTS,
        packs=VALID_PACKS if kind == "review_packs" else None,
    )


def _entry(target: Any, kind: str) -> Path:
    path = target.repo / ".aiops" / SOURCE_FILE[kind]
    if path.exists() or path.is_symlink():
        path.unlink()
    return path


def _outside(tmp_path: Path, kind: str) -> Path:
    path = tmp_path / "outside-host" / f"{kind}.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(OUTSIDE_BODY[kind], encoding="utf-8")
    return path


def _no_marker_anywhere(root: Path) -> None:
    for item in sorted(root.rglob("*")):
        if item.is_file():
            assert MARKER not in item.read_text(encoding="utf-8", errors="ignore"), item.name


def _in_subprocess(code: str, *, timeout: int = 40) -> subprocess.CompletedProcess[str]:
    """A FIFO must never block the loader: run it under a hard wall-clock limit."""
    prelude = f"import sys, warnings; warnings.simplefilter('ignore'); sys.path.insert(0, {str(REPO_ROOT)!r})\n"
    return subprocess.run([sys.executable, "-I", "-c", prelude + code], capture_output=True, text=True, timeout=timeout)


# ------------------------------------------------------------------------------------------------------------------
# Contract sources: hostile entry -> typed INVALID -> critical limitation -> plan degraded -> gate not passed
# ------------------------------------------------------------------------------------------------------------------

HOSTILE: dict[str, Any] = {
    "dangling_symlink": lambda path, tmp, kind: path.symlink_to(tmp / "gone.yaml"),
    "external_symlink": lambda path, tmp, kind: path.symlink_to(_outside(tmp, kind)),
    "hard_link_to_outside_file": lambda path, tmp, kind: os.link(_outside(tmp, kind), path),
    "directory_in_place_of_file": lambda path, tmp, kind: path.mkdir(),
    "invalid_utf8": lambda path, tmp, kind: path.write_bytes(b"rules: [\xff\xfe]\n"),
    "construction_error_timestamp": lambda path, tmp, kind: path.write_text("c1: 2001-13-45\n", encoding="utf-8"),
    "construction_error_tag": lambda path, tmp, kind: path.write_text("c1: !!int abc\n", encoding="utf-8"),
    "parser_recursion": lambda path, tmp, kind: path.write_text("[" * 3000 + "]" * 3000 + "\n", encoding="utf-8"),
    "cyclic_anchor": lambda path, tmp, kind: path.write_text("a: &a [*a]\n", encoding="utf-8"),
    "alias_bomb_9x9": lambda path, tmp, kind: path.write_text(
        "\n".join(["a0: &a0 [x,x,x,x,x,x,x,x,x]"] + [f"a{i}: &a{i} [" + ",".join([f"*a{i-1}"] * 9) + "]" for i in range(1, 10)]) + "\n",
        encoding="utf-8",
    ),
    "depth_65": lambda path, tmp, kind: path.write_text("[" * 65 + "]" * 65 + "\n", encoding="utf-8"),
}


@pytest.mark.parametrize("kind", list(SOURCE_FILE))
@pytest.mark.parametrize("hostile", list(HOSTILE))
def test_hostile_contract_source_is_invalid_critical_and_the_gate_does_not_pass(tmp_path: Path, kind: str, hostile: str) -> None:
    target = _target(tmp_path, "t", kind)
    HOSTILE[hostile](_entry(target, kind), tmp_path, kind)
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert loaded.status == "degraded" and any(item.startswith(f"{kind}_") for item in loaded.limitations), loaded.limitations
    run = canonical_run(target)
    assert CRITICAL[kind] in run.plan.limitations and run.plan.status == "degraded", run.plan.limitations
    paths = cli_chain(target, tmp_path / "chain")
    assert read_json(paths["gate"])["status"] != "passed"
    _no_marker_anywhere(tmp_path / "chain")


@pytest.mark.parametrize("kind", list(SOURCE_FILE))
def test_contract_source_positive_controls_stay_conclusive(tmp_path: Path, kind: str) -> None:
    """Genuinely absent and genuinely valid sources keep the compatible, conclusive behaviour."""
    for label in ("valid", "absent"):
        target = _target(tmp_path, label, kind)
        if label == "absent":
            _entry(target, kind)
            if kind == "domain_contracts":
                (target.repo / ".aiops" / "review-packs.yaml").unlink(missing_ok=True)
        loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
        assert loaded.status == "complete" and loaded.limitations == [], (label, loaded.limitations)
        run = canonical_run(target)
        assert not any(item.startswith("invalid_source_") for item in run.plan.limitations), (label, run.plan.limitations)
        assert read_json(cli_chain(target, tmp_path / f"chain-{label}")["gate"])["status"] == "passed", label


@pytest.mark.parametrize("kind", list(SOURCE_FILE))
def test_oversize_contract_source_is_typed_and_never_truncated(tmp_path: Path, kind: str) -> None:
    target = _target(tmp_path, "t", kind)
    _entry(target, kind).write_bytes(b"#" * (2 * 1024 * 1024 + 1))
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert f"{kind}_source_oversize" in loaded.limitations
    assert CRITICAL[kind] in canonical_run(target).plan.limitations


@pytest.mark.parametrize("kind", list(SOURCE_FILE))
def test_fifo_contract_source_never_blocks_and_is_invalid(tmp_path: Path, kind: str) -> None:
    target = _target(tmp_path, "t", kind)
    os.mkfifo(_entry(target, kind))
    result = _in_subprocess(
        "from pathlib import Path\nfrom app.agent_review.repo_profile import load_repo_profile\n"
        f"r = load_repo_profile(Path({str(target.repo)!r}), target_repo={DOMAIN.target_repo!r})\n"
        "print(r.status, r.limitations)\n"
    )
    assert result.returncode == 0, result.stderr
    assert f"{kind}_source_unsafe" in result.stdout, result.stdout


def test_dangling_link_is_not_the_same_state_as_an_absent_file(tmp_path: Path) -> None:
    """The measured false-complete of #375 (C1): absent -> complete/passed; dangling used to be identical."""
    absent = _target(tmp_path, "absent", "domain_contracts")
    _entry(absent, "domain_contracts")
    dangling = _target(tmp_path, "dangling", "domain_contracts")
    _entry(dangling, "domain_contracts").symlink_to(tmp_path / "gone.yaml")
    assert read_json(cli_chain(absent, tmp_path / "a")["gate"])["status"] == "passed"
    dangling_gate = read_json(cli_chain(dangling, tmp_path / "d")["gate"])["status"]
    assert dangling_gate != "passed"


# ------------------------------------------------------------------------------------------------------------------
# repo-profile.yaml
# ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", ["dangling_symlink", "external_symlink", "hard_link_to_outside_file", "directory_in_place_of_file"])
def test_unsafe_repo_profile_is_a_failed_profile_never_profile_missing(tmp_path: Path, hostile: str) -> None:
    target = _target(tmp_path, "t")
    profile = target.repo / ".aiops" / "repo-profile.yaml"
    body = profile.read_text(encoding="utf-8")
    profile.unlink()
    outside = tmp_path / "outside-profile.yaml"
    outside.write_text(body + f"owner: {MARKER}\n", encoding="utf-8")
    {
        "dangling_symlink": lambda: profile.symlink_to(tmp_path / "gone.yaml"),
        "external_symlink": lambda: profile.symlink_to(outside),
        "hard_link_to_outside_file": lambda: os.link(outside, profile),
        "directory_in_place_of_file": lambda: profile.mkdir(),
    }[hostile]()
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert loaded.status == "failed" and "repo_profile_missing" not in loaded.limitations
    assert any(item.startswith("repo_profile_source_") for item in loaded.limitations), loaded.limitations
    code, written = _intake_cli(target, tmp_path / "out")
    assert code == 1
    assert all(MARKER not in text for text in written.values())


def test_missing_repo_profile_keeps_its_degraded_compatibility_state(tmp_path: Path) -> None:
    target = _target(tmp_path, "t")
    (target.repo / ".aiops" / "repo-profile.yaml").unlink()
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert loaded.status == "degraded" and loaded.limitations == ["repo_profile_missing"]


# ------------------------------------------------------------------------------------------------------------------
# C6: empty / comments-only / null -- compatibility is PRESERVED; the policy is a pending maintainer decision
# ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", list(SOURCE_FILE))
@pytest.mark.parametrize("text", ["", "# only a comment\n", "null\n", "~\n", "{}\n", "[]\n"])
def test_empty_null_and_comment_only_sources_keep_the_existing_compatible_reading(tmp_path: Path, kind: str, text: str) -> None:
    """CHARACTERISATION, not endorsement: `yaml.safe_load(...) or {}` reads these as an empty declaration, exactly as
    before this slice. Whether a PRESENT-but-empty contract file should be INVALID is a normative decision that is
    recorded as pending (STOP_POLICY_DECISION_REQUIRED for this sub-item), not invented here."""
    target = _target(tmp_path, "t", kind)
    if kind == "review_packs":
        _entry(target, "domain_contracts")  # an empty packs file must not reference a contract that no longer exists
    _entry(target, kind).write_text(text, encoding="utf-8")
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert not any(item.startswith(f"{kind}_") for item in loaded.limitations), loaded.limitations


# ------------------------------------------------------------------------------------------------------------------
# Structural scan: iterative, memoised, expanded-node bound, cycle detection
# ------------------------------------------------------------------------------------------------------------------

def _nest(depth: int) -> Any:
    node: Any = "leaf"
    for _ in range(depth):
        node = [node]
    return node


def test_scan_depth_boundary_64_accepted_65_rejected() -> None:
    assert scan_structure(_nest(64)).limit is None
    assert scan_structure(_nest(65)).limit == STRUCTURE_DEPTH


def test_scan_node_boundary_50000_accepted_50001_rejected() -> None:
    assert scan_structure(["x"] * 49_999).limit is None  # container + 49 999 leaves = 50 000
    assert scan_structure(["x"] * 50_000).limit == STRUCTURE_NODES


def test_scan_is_iterative_not_recursive_on_depth() -> None:
    """A 200 000-deep structure is rejected by the depth bound without any RecursionError."""
    assert scan_structure(_nest(200_000)).limit == STRUCTURE_DEPTH


def test_scan_counts_aliases_with_multiplicity_and_accepts_legitimate_sharing() -> None:
    bomb = yaml.safe_load("\n".join(["a0: &a0 [x,x,x,x,x,x,x,x,x]"] + [f"a{i}: &a{i} [" + ",".join([f"*a{i-1}"] * 9) + "]" for i in range(1, 10)]))
    assert scan_structure(bomb).limit == STRUCTURE_NODES
    shared = yaml.safe_load("base: &b [1, 2, 3]\nitems:\n  - *b\n  - *b\n  - *b\n")
    assert scan_structure(shared).limit is None


def test_scan_wide_alias_dag_is_bounded_in_work_not_only_in_verdict() -> None:
    """A wide DAG with 40 levels of fan-out 2 has 2**40 expanded nodes but ~80 unique containers."""
    layer: Any = ["x"]
    for _ in range(40):
        layer = [layer, layer]
    assert scan_structure(layer).limit == STRUCTURE_NODES


def test_scan_cycle_is_rejected_and_a_dag_is_not_a_cycle() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    assert scan_structure(cyclic).limit == STRUCTURE_CYCLE
    mapping: dict[str, Any] = {}
    mapping["self"] = {"deeper": mapping}
    assert scan_structure(mapping).limit == STRUCTURE_CYCLE
    shared = ["x"]
    assert scan_structure({"a": shared, "b": shared, "c": [shared, shared]}).limit is None


def test_scan_reports_non_string_mapping_keys_at_any_depth() -> None:
    assert scan_structure({"a": [{"b": {1: "x"}}]}).has_non_string_key is True
    assert scan_structure({"a": [{"b": {"c": "x"}}]}).has_non_string_key is False


def _reference(node: Any, path: tuple[int, ...] = ()) -> tuple[int, int, bool]:
    """Naive recursive oracle: (expanded nodes, height, has_cycle)."""
    if not isinstance(node, (dict, list)):
        return 1, 0, False
    if id(node) in path:
        return 0, 0, True
    values = list(node.values()) if isinstance(node, dict) else list(node)
    total, height, cyclic = 1 + (len(node) if isinstance(node, dict) else 0), 0, False
    for value in values:
        child_nodes, child_height, child_cycle = _reference(value, (*path, id(node)))
        total += child_nodes
        height = max(height, child_height)
        cyclic = cyclic or child_cycle
    return total, height + 1, cyclic


@pytest.mark.parametrize("limits", [(4, 20), (6, 60), (8, 200), (10, 1_000), (64, 50_000)])
def test_scan_agrees_with_a_naive_oracle_on_random_dags(limits: tuple[int, int]) -> None:
    max_depth, max_nodes = limits
    rng = random.Random(375)
    checked = 0
    for _ in range(400):
        pool: list[Any] = [["leaf", 1], {"k": "v"}]
        for _ in range(rng.randint(2, 7)):
            children = [rng.choice(pool) if rng.random() < 0.7 else rng.choice(["s", 3, None]) for _ in range(rng.randint(1, 4))]
            pool.append(children if rng.random() < 0.5 else {f"k{index}": child for index, child in enumerate(children)})
        document = pool[-1]
        nodes, height, cyclic = _reference(document)
        expected = None if not cyclic and nodes <= max_nodes and height <= max_depth else "limit"
        actual = scan_structure(document, max_depth=max_depth, max_nodes=max_nodes).limit
        assert (actual is None) == (expected is None), (limits, nodes, height, actual)
        checked += 1
    assert checked == 400


# ------------------------------------------------------------------------------------------------------------------
# acquire_source states
# ------------------------------------------------------------------------------------------------------------------

def test_acquire_source_state_table(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "ok.txt").write_text("hello", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text(MARKER, encoding="utf-8")
    (tmp_path / "d" / "link.txt").symlink_to(outside)
    (tmp_path / "d" / "dangling.txt").symlink_to(tmp_path / "nowhere")
    os.link(outside, tmp_path / "d" / "hard.txt")
    (tmp_path / "d" / "big.txt").write_bytes(b"x" * 100)
    states = {name: acquire_source(tmp_path, name, max_bytes=50) for name in ("d/ok.txt", "d/nope.txt", "nodir/x", "d/link.txt", "d/dangling.txt", "d/hard.txt", "d/big.txt", "d")}
    assert states["d/ok.txt"].state == SOURCE_STATE_PRESENT and states["d/ok.txt"].data == b"hello"
    assert states["d/nope.txt"].state == SOURCE_STATE_ABSENT and states["nodir/x"].state == SOURCE_STATE_ABSENT
    for name in ("d/link.txt", "d/dangling.txt", "d/hard.txt", "d"):
        assert states[name].state == SOURCE_STATE_UNSAFE and states[name].data is None, name
    assert states["d/big.txt"].state == SOURCE_STATE_OVERSIZE and states["d/big.txt"].data is None


# ------------------------------------------------------------------------------------------------------------------
# Declared artifacts (agent_dir root): keep missing / invalid / REQUIRED semantics, close the unsafe states
# ------------------------------------------------------------------------------------------------------------------

def _load_one(agent_dir: Path, path: str, kind: str = "json", required: bool = False) -> Any:
    return load_declared_artifacts(
        agent_dir=agent_dir,
        declarations=[ArtifactDeclaration(name="a", path=path, kind=kind, required=required)],
        redaction_state=RedactionState(),
    )


def test_artifact_dangling_link_inside_the_root_is_invalid_not_missing(tmp_path: Path) -> None:
    (tmp_path / "a.json").symlink_to(tmp_path / "gone.json")
    result = _load_one(tmp_path, "a.json")
    assert result.artifact_status[0].status == "invalid" and result.artifact_status[0].error_class == "artifact_unsafe"
    assert "artifact_invalid:a" in result.limitations and result.artifacts == {}


def test_artifact_hard_link_to_an_outside_file_is_refused_without_reading_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{"k": "%s"}' % MARKER, encoding="utf-8")
    os.link(outside, tmp_path / "a.json")
    result = _load_one(tmp_path, "a.json")
    assert result.artifact_status[0].status == "invalid" and result.artifacts == {}
    assert MARKER not in repr(result.artifact_status)


def test_artifact_intermediate_directory_link_is_refused(tmp_path: Path) -> None:
    real = tmp_path.parent / f"{tmp_path.name}-realdir"
    real.mkdir()
    (real / "x.json").write_text('{"k": "%s"}' % MARKER, encoding="utf-8")
    (tmp_path / "sub").symlink_to(real, target_is_directory=True)
    result = _load_one(tmp_path, "sub/x.json")
    assert result.artifact_status[0].status == "invalid" and result.artifacts == {}


def test_artifact_fifo_never_blocks(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "a.json")
    result = _in_subprocess(
        "from pathlib import Path\nfrom app.agent_review.artifact_loader import load_declared_artifacts\n"
        "from app.agent_review.redaction import RedactionState\nfrom app.agent_review.schemas import ArtifactDeclaration\n"
        f"r = load_declared_artifacts(agent_dir=Path({str(tmp_path)!r}), declarations=[ArtifactDeclaration(name='a', path='a.json', kind='json')], redaction_state=RedactionState())\n"
        "print(r.artifact_status[0].status, r.artifact_status[0].error_class)\n"
    )
    assert result.returncode == 0 and "invalid" in result.stdout, result.stdout + result.stderr


def test_artifact_oversize_is_typed_never_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.json").write_text('{"k": "' + "x" * 200 + '"}', encoding="utf-8")
    monkeypatch.setattr(artifact_loader, "ARTIFACT_MAX_BYTES", 100)
    result = _load_one(tmp_path, "a.json")
    assert result.artifact_status[0].error_class == "artifact_oversize" and result.artifacts == {}


@pytest.mark.parametrize(
    "kind,text",
    [
        ("yaml", "a: &a [*a]\n"),
        ("yaml", "\n".join(["a0: &a0 [x,x,x,x,x,x,x,x,x]"] + [f"a{i}: &a{i} [" + ",".join([f"*a{i-1}"] * 9) + "]" for i in range(1, 10)]) + "\n"),
        ("yaml", "[" * 65 + "]" * 65 + "\n"),
        ("json", "[" * 3000 + "]" * 3000),
    ],
)
def test_artifact_structure_limit_is_typed_before_redaction_can_expand_it(tmp_path: Path, kind: str, text: str) -> None:
    (tmp_path / f"a.{kind}").write_text(text, encoding="utf-8")
    result = _load_one(tmp_path, f"a.{kind}", kind=kind)
    assert result.artifact_status[0].status == "invalid" and result.artifact_status[0].error_class == "artifact_structure_limit"


def test_artifact_missing_semantics_are_preserved(tmp_path: Path) -> None:
    optional = _load_one(tmp_path, "nope.json", required=False)
    required = _load_one(tmp_path, "nope.json", required=True)
    assert optional.artifact_status[0].status == "missing" and optional.limitations == []
    assert required.artifact_status[0].status == "missing" and "required_artifact_missing:a" in required.limitations
    (tmp_path / "ok.json").write_text('{"k": 1}', encoding="utf-8")
    assert _load_one(tmp_path, "ok.json").artifact_status[0].status == "available"


def test_artifact_chain_unsafe_optional_artifact_reaches_intake_and_leaves_no_marker(tmp_path: Path) -> None:
    target = _target(tmp_path, "t")
    optional = target.agent / "file-diff-context.json"
    optional.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"files": [{"path": "%s"}]}' % MARKER, encoding="utf-8")
    os.link(outside, optional)
    code, written = _intake_cli(target, tmp_path / "out")
    assert code == 0, written["<stdout>"]
    assert '"status": "degraded"' in written["intake.json"] and "artifact_unsafe" in written["intake.json"]
    assert all(MARKER not in text for text in written.values())


# ------------------------------------------------------------------------------------------------------------------
# Single reader authority: no second implementation of containment
# ------------------------------------------------------------------------------------------------------------------

def test_every_source_reader_uses_the_one_acquisition_authority() -> None:
    import app.agent_review.repo_profile as repo_profile

    for module in (repo_profile, artifact_loader):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "acquire_source(" in source, module.__name__
        for forbidden in (".read_text(", ".exists()", "os.path.lexists", ".resolve()"):
            assert forbidden not in source, (module.__name__, forbidden)


# ------------------------------------------------------------------------------------------------------------------
# MethodGuidance sidecar: the structural bound applies to the sidecar parse too (found by mutant M12)
# ------------------------------------------------------------------------------------------------------------------

SIDECAR_STRUCTURES = {
    "cyclic_anchor": "version: 1\nsources: &s [*s]\nguidance: []\n",
    "depth_65": "version: 1\nsources: []\nguidance: []\nextra: " + "[" * 65 + "]" * 65 + "\n",
    "alias_bomb": "\n".join(["a0: &a0 [x,x,x,x,x,x,x,x,x]"] + [f"a{i}: &a{i} [" + ",".join([f"*a{i-1}"] * 9) + "]" for i in range(1, 10)]) + "\n",
}


@pytest.mark.parametrize("shape", list(SIDECAR_STRUCTURES))
def test_method_guidance_sidecar_structure_limit_is_invalid_and_never_carried(tmp_path: Path, shape: str) -> None:
    """Counter-model: the sidecar parse is exempt from the structural bound, so a cyclic or alias-expanding document
    is carried into `TargetProfile.method_guidance` and expanded by the first serialisation."""
    target = make_target(tmp_path, DOMAIN, method_guidance=SIDECAR_STRUCTURES[shape])
    loaded = load_repo_profile(target.repo, target_repo=DOMAIN.target_repo)
    assert "method_guidance_yaml_invalid" in loaded.limitations, loaded.limitations
    assert loaded.profile.method_guidance == []
    assert "invalid_source_method_guidance:MALFORMED_SHAPE" in canonical_run(target).plan.limitations
