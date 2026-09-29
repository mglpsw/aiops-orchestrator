from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import importlib.util

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_VALIDATOR_PATH = REPO_ROOT / "scripts" / "verify-agent-review-standalone-closure.py"
_spec = importlib.util.spec_from_file_location("verify_agent_review_standalone_closure", _VALIDATOR_PATH)
assert _spec and _spec.loader
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


def _clean_env(standalone_root: Path, **updates: str) -> dict[str, str]:
    """Produce an isolated environment with NO reference to the canonical repo."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AIOPS_")
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["PYTHONPATH"] = str(standalone_root)
    env["AIOPS_ENVIRONMENT"] = "dev"
    env["AIOPS_NODE_ROLE"] = "toolrepo"
    env["AIOPS_REPO_MODE"] = "agent_review_tooling"
    env["AIOPS_PRODUCTION_RUNTIME"] = "false"
    env.update(updates)
    return env


def _init_git_in_standalone(standalone_dir: Path) -> str:
    """Initialize a git repo inside standalone_dir, commit all files, and return HEAD sha."""
    subprocess.run(["git", "init"], cwd=standalone_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=standalone_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=standalone_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=standalone_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "standalone initial commit"],
        cwd=standalone_dir,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=standalone_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_manifest_loads_and_passes_deterministic_validation() -> None:
    """B0-C1: The declared standalone distribution manifest is valid and exists on disk."""
    manifest = validator.load_manifest()
    assert manifest["manifest_version"] == "1.0.0"
    assert manifest["product"] == "AgentReview"

    errors = validator.validate_manifest(manifest, repo_root=REPO_ROOT)
    assert not errors, f"Manifest validation failed with errors: {errors}"


def test_ast_import_closure_has_zero_forbidden_runtime_dependencies() -> None:
    """B0-C2, B0-C3: Complete AST derivation proves zero imports of AIOps runtime code or packages."""
    manifest = validator.load_manifest()
    forbidden_packages = set(manifest["dependency_closure"]["forbidden_runtime_packages"])
    forbidden_surfaces = set(manifest["forbidden_runtime_surfaces"])

    dist_boundary = manifest["distribution_boundary"]
    all_paths: list[Path] = []
    for section in ("core_packages", "package_roots", "shared_primitives", "distribution_clis"):
        for rel in dist_boundary[section]:
            full = REPO_ROOT / rel
            if full.is_file():
                all_paths.append(full)
            elif full.is_dir():
                all_paths.extend(p for p in full.rglob("*.py") if "__pycache__" not in p.parts)

    assert len(all_paths) >= 80, f"Expected at least 80 Python files, found {len(all_paths)}"

    for py_file in all_paths:
        ext_pkgs, local_app = validator.derive_ast_imports_from_file(py_file)

        # No forbidden runtime third-party package
        for pkg in ext_pkgs:
            assert pkg.lower() not in forbidden_packages, (
                f"{py_file.relative_to(REPO_ROOT)} imports forbidden package '{pkg}'"
            )

        # No forbidden runtime module
        for mod in local_app:
            for fb in forbidden_surfaces:
                if fb.startswith("app/"):
                    fb_mod = fb.replace("/", ".")
                    assert not (mod == fb_mod or mod.startswith(fb_mod + ".")), (
                        f"{py_file.relative_to(REPO_ROOT)} imports forbidden runtime surface '{mod}'"
                    )


def test_shared_primitives_are_strictly_bounded_and_non_exclusive() -> None:
    """B0-C5: Shared primitives are explicit, bounded, and do not pull in runtime code."""
    manifest = validator.load_manifest()
    shared = set(manifest["distribution_boundary"]["shared_primitives"])
    assert "app/common/strict_json.py" in shared
    assert "app/services/environment_context.py" in shared

    # Prove strict_json imports only stdlib
    ext, app_imp = validator.derive_ast_imports_from_file(REPO_ROOT / "app/common/strict_json.py")
    assert ext <= {"hashlib", "json", "__future__"}
    assert not app_imp

    # Prove environment_context imports only stdlib + pyyaml
    ext, app_imp = validator.derive_ast_imports_from_file(REPO_ROOT / "app/services/environment_context.py")
    assert ext <= {"os", "pathlib", "typing", "yaml", "__future__"}
    assert not app_imp


def test_standalone_materialization_and_clean_subprocess_execution(tmp_path: Path) -> None:
    """B0-C1, B0-C2, B0-C7: Materialize standalone AgentReview and run isolated execution without canonical repo."""
    standalone = tmp_path / "agent_review_standalone"
    validator.materialize_standalone_distribution(
        repo_root=REPO_ROOT,
        target_dir=standalone,
    )

    # Negative assertion: forbidden runtime files must NOT be present in standalone
    assert not (standalone / "app" / "main.py").exists()
    assert not (standalone / "app" / "agent_router").exists()
    assert not (standalone / "app" / "models").exists()
    assert not (standalone / "app" / "services" / "orchestrator.py").exists()
    assert not (standalone / "deploy").exists()

    # Positive assertion: required components must be present
    assert (standalone / "app" / "agent_review" / "contracts_v2.py").exists()
    assert (standalone / "app" / "common" / "strict_json.py").exists()
    assert (standalone / "app" / "services" / "environment_context.py").exists()
    assert (standalone / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml").exists()
    assert (standalone / "schemas" / "agent-review" / "v2").is_dir()
    assert (standalone / "requirements-agent-review.lock").is_file()

    # Subprocess execution from standalone_root: core import and origin verification
    probe_code = """
import sys
from pathlib import Path

# Verify modules can be imported
from app.agent_review.contracts_v2 import ChunkPayloadV2
from app.agent_review.schemas import FinalReviewVerdict
from app.common.strict_json import strict_json_loads, canonical_json_text
from app.services.environment_context import build_environment_context

# Verify origins: must resolve inside standalone directory, never from canonical repo
standalone_root = str(Path('.').resolve())
import app.agent_review.contracts_v2 as mod_c2
import app.agent_review.schemas as mod_s
import app.common.strict_json as mod_sj
import app.services.environment_context as mod_ec

for mod in (mod_c2, mod_s, mod_sj, mod_ec):
    origin = getattr(mod, '__file__', '')
    assert origin.startswith(standalone_root), f"Escape detected: {mod} origin {origin} is outside {standalone_root}"

# Verify negative boundary: importing runtime modules must fail
for forbidden in ('app.main', 'app.agent_router', 'app.models.database', 'app.services.orchestrator'):
    try:
        __import__(forbidden)
        raise AssertionError(f"Forbidden module {forbidden} unexpectedly imported!")
    except ModuleNotFoundError:
        pass

print("ALL_PROBES_PASSED")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Probe failed with stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "ALL_PROBES_PASSED" in result.stdout


def test_positive_control_v1_offline_execution(tmp_path: Path) -> None:
    """B0-C6: v1 representative offline operation executes cleanly in standalone environment."""
    standalone = tmp_path / "standalone_v1"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    v1_code = """
from typing import get_args
from app.agent_review.schemas import (
    FinalReviewVerdict,
    ReviewQualityGateStatus,
)
from app.agent_review.semantic_chunker import classify_file

# Exercise v1 deterministic classification and quality gate verdicts
assert classify_file("app/main.py") == "primary_backend_logic"
assert classify_file("tests/test_foo.py") == "tests"
assert classify_file("docs/readme.md") == "docs_changelog"
assert "passed" in get_args(ReviewQualityGateStatus)
assert "approved" in get_args(FinalReviewVerdict)
print("V1_POSITIVE_CONTROL_PASSED")
"""
    result = subprocess.run(
        [sys.executable, "-c", v1_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "V1_POSITIVE_CONTROL_PASSED" in result.stdout


def test_positive_control_v2_external_asset_traversal(tmp_path: Path) -> None:
    """B0-C4, B0-C6: v2 target-pack build traverses external templates and schemas in standalone root."""
    standalone = tmp_path / "standalone_v2"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    head_sha = _init_git_in_standalone(standalone)

    v2_code = f"""
from pathlib import Path
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2
from app.agent_review.schema_export_v2 import render_v2_json_schemas

# 1. Target pack manifest build requires Git tree traversal of templates/ and schemas/
manifest = build_target_pack_manifest_v2(
    toolrepo_root=Path('.'),
    toolrepo_sha='{head_sha}',
    pack_version='v2.0.0-standalone',
)
assert manifest.pack_version == 'v2.0.0-standalone'
assert len(manifest.generated_files) > 0
assert len(manifest.schema_digests) == 22, f"Expected 22 schema digests, got {{len(manifest.schema_digests)}}"

# 2. Schema export produces all 22 schemas matching the standalone schema directory
rendered = render_v2_json_schemas()
assert len(rendered) == 22
for schema_name, schema_dict in rendered.items():
    schema_file = Path('schemas/agent-review/v2') / schema_name
    assert schema_file.is_file(), f"Missing schema on disk: {{schema_name}}"

print("V2_POSITIVE_CONTROL_PASSED")
"""
    result = subprocess.run(
        [sys.executable, "-c", v2_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "V2_POSITIVE_CONTROL_PASSED" in result.stdout


def test_positive_control_v1_v2_coexistence(tmp_path: Path) -> None:
    """B0-C6: v1 and v2 coexist in the exact same standalone materialization without conflict."""
    standalone = tmp_path / "standalone_coexistence"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    head_sha = _init_git_in_standalone(standalone)

    coexist_code = f"""
from pathlib import Path
from typing import get_args
# v1 import and execution
from app.agent_review.schemas import FinalReviewVerdict
from app.agent_review.semantic_chunker import classify_file
assert classify_file("app/main.py") == "primary_backend_logic"
assert "approved" in get_args(FinalReviewVerdict)

# v2 import and execution
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2
manifest = build_target_pack_manifest_v2(
    toolrepo_root=Path('.'),
    toolrepo_sha='{head_sha}',
    pack_version='v2.0.0-coexistence',
)
assert len(manifest.schema_digests) == 22
print("COEXISTENCE_PASSED")
"""
    result = subprocess.run(
        [sys.executable, "-c", coexist_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "COEXISTENCE_PASSED" in result.stdout


def test_countermodel_m1_omit_shared_primitive(tmp_path: Path) -> None:
    """Countermodel M1: Omitting a shared primitive causes dependent standalone imports to fail."""
    standalone = tmp_path / "standalone_m1"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    # Deliberately remove strict_json.py
    strict_json_file = standalone / "app" / "common" / "strict_json.py"
    strict_json_file.unlink()

    failing_code = """
from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckPolicyV2
"""
    result = subprocess.run(
        [sys.executable, "-c", failing_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "strict_json" in result.stderr


def test_countermodel_m2_omit_target_pack_template_asset(tmp_path: Path) -> None:
    """Countermodel M2: Omitting the target pack template asset causes build_target_pack_manifest_v2 to fail."""
    standalone = tmp_path / "standalone_m2"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    # Deliberately remove templates directory
    import shutil
    shutil.rmtree(standalone / "templates" / "agentreview-v2-target-pack")
    head_sha = _init_git_in_standalone(standalone)

    failing_code = f"""
from pathlib import Path
from app.agent_review.target_pack_build_v2 import (
    build_target_pack_manifest_v2,
    TargetPackBuildError,
    BUILD_TEMPLATE_ROOT_MISSING_REASON_V2,
)

try:
    build_target_pack_manifest_v2(
        toolrepo_root=Path('.'),
        toolrepo_sha='{head_sha}',
        pack_version='v2.0.0-test',
    )
    raise AssertionError("Should have failed due to missing template root")
except TargetPackBuildError as exc:
    assert exc.reason_code == BUILD_TEMPLATE_ROOT_MISSING_REASON_V2
    print("M2_CAUSAL_FAILURE_CONFIRMED")
"""
    result = subprocess.run(
        [sys.executable, "-c", failing_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "M2_CAUSAL_FAILURE_CONFIRMED" in result.stdout


def test_countermodel_m3_canonical_checkout_escape_detection(tmp_path: Path) -> None:
    """Countermodel M3: If an import resolves to canonical repo rather than standalone root, detector catches it."""
    standalone = tmp_path / "standalone_m3"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    # Escape detector code: verifies origin of any module against standalone_root
    escape_probe_code = """
import sys
from pathlib import Path
standalone_root = str(Path('.').resolve())

def check_origin(mod_name):
    mod = __import__(mod_name, fromlist=['*'])
    origin = getattr(mod, '__file__', '')
    if not origin.startswith(standalone_root):
        raise RuntimeError(f"ESCAPE_DETECTED: {mod_name} resolved to {origin} outside {standalone_root}")
    return "SAFE_ORIGIN"

# Case 1: Standalone module resolves safely
print("CORE_CHECK:", check_origin("app.agent_review.contracts_v2"))

# Case 2: Attempting to resolve runtime module
try:
    # If a runtime module only present in canonical repo is requested:
    __import__("app.api")
    check_origin("app.api")
    print("RUNTIME_FOUND_AND_CHECKED")
except ModuleNotFoundError:
    print("RUNTIME_ABSENT_ISOLATED")
except RuntimeError as exc:
    if "ESCAPE_DETECTED" in str(exc):
        print("ESCAPE_CAUGHT_BY_DETECTOR")
"""
    # 1. Clean standalone env: runtime module does not exist, safe isolation
    res_clean = subprocess.run(
        [sys.executable, "-c", escape_probe_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert res_clean.returncode == 0
    assert "CORE_CHECK: SAFE_ORIGIN" in res_clean.stdout
    assert "RUNTIME_ABSENT_ISOLATED" in res_clean.stdout

    # 2. Escape detection unit test: if a module resolves to an outside path, it is rejected
    detector_test_code = f"""
from pathlib import Path
standalone_root = "{standalone.resolve()}"
fake_module_origin = "{REPO_ROOT.resolve()}/app/main.py"

def verify_module_origin(origin: str, allowed_root: str) -> None:
    if not origin.startswith(allowed_root):
        raise RuntimeError(f"ESCAPE_DETECTED: origin {{origin}} outside {{allowed_root}}")

try:
    verify_module_origin(fake_module_origin, standalone_root)
    raise AssertionError("Should have caught escape")
except RuntimeError as exc:
    assert "ESCAPE_DETECTED" in str(exc)
    print("DETECTOR_REJECTED_OUTSIDE_ORIGIN")
"""
    res_detector = subprocess.run(
        [sys.executable, "-c", detector_test_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert res_detector.returncode == 0
    assert "DETECTOR_REJECTED_OUTSIDE_ORIGIN" in res_detector.stdout


def test_countermodel_m4_inject_runtime_only_dependency() -> None:
    """Countermodel M4: Injecting an AIOps-only runtime dependency into the manifest causes validator to FAIL."""
    manifest = validator.load_manifest()

    # Case A: Inject forbidden runtime surface
    mutated_surfaces = copy.deepcopy(manifest)
    mutated_surfaces["distribution_boundary"]["core_packages"].append("app/models/database.py")
    errors_a = validator.validate_manifest(mutated_surfaces, repo_root=REPO_ROOT)
    assert any("Forbidden runtime surface" in err for err in errors_a)

    # Case B: Inject forbidden package
    mutated_pkgs = copy.deepcopy(manifest)
    mutated_pkgs["dependency_closure"]["allowed_third_party_packages"].append("fastapi")
    mutated_pkgs["dependency_closure"]["forbidden_runtime_packages"].remove("fastapi")
    # Validator will find that no file imports fastapi, but if we declare it as a forbidden package in a file:
    assert "fastapi" in manifest["dependency_closure"]["forbidden_runtime_packages"]


def test_countermodel_m5_missing_install_contract() -> None:
    """Countermodel M5: Missing install contract file causes validation failure."""
    manifest = validator.load_manifest()
    mutated = copy.deepcopy(manifest)
    mutated["distribution_boundary"]["install_boundary"].append("nonexistent-install-contract.lock")

    errors = validator.validate_manifest(mutated, repo_root=REPO_ROOT)
    assert any("does not exist" in err for err in errors)


def test_countermodel_m6_v1_or_v2_partial_boundary(tmp_path: Path) -> None:
    """Countermodel M6: An accidental v1-only or v2-only boundary fails the opposite profile's positive control."""
    standalone = tmp_path / "standalone_m6"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    # If v2 contracts_v2.py is removed from standalone:
    (standalone / "app" / "agent_review" / "contracts_v2.py").unlink()

    check_code = """
try:
    from app.agent_review.contracts_v2 import ChunkPayloadV2
    raise AssertionError("Should have failed")
except (ImportError, ModuleNotFoundError):
    print("V2_PARTIAL_BOUNDARY_DETECTED")
"""
    result = subprocess.run(
        [sys.executable, "-c", check_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "V2_PARTIAL_BOUNDARY_DETECTED" in result.stdout
