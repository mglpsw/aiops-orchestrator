from __future__ import annotations

import copy
import json
import os
import shutil
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
        and key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE")
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
                    fb_mod = fb.replace("/", ".").removesuffix(".py")
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
    _run_core_probe(standalone)


def _run_core_probe(standalone: Path, python_executable: str = sys.executable) -> None:
    probe_code = """
import sys
from pathlib import Path

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

# Verify modules can be imported
from app.agent_review.contracts_v2 import ChunkPayloadV2
from app.agent_review.schemas import FinalReviewVerdict
from app.agent_review.semantic_chunker import classify_file
from app.agent_review.authoritative_check_policy_v2 import load_authoritative_check_policy_v2
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2
from app.agent_review.schema_export_v2 import render_v2_json_schemas
from app.agent_review.cli import build_intake
from app.common.strict_json import strict_json_loads, canonical_json_text
from app.services.environment_context import build_environment_context

# Verify origins: must resolve inside standalone directory, never from canonical repo
standalone_root = Path('.').resolve()
import app.agent_review.contracts_v2 as mod_c2
import app.agent_review.schemas as mod_s
import app.agent_review.semantic_chunker as mod_sc
import app.agent_review.authoritative_check_policy_v2 as mod_ac
import app.agent_review.target_pack_build_v2 as mod_tp
import app.agent_review.schema_export_v2 as mod_se
import app.agent_review.cli as mod_cli
import app.common.strict_json as mod_sj
import app.services.environment_context as mod_ec

for mod in (mod_c2, mod_s, mod_sc, mod_ac, mod_tp, mod_se, mod_cli, mod_sj, mod_ec):
    origin = getattr(mod, '__file__', None)
    require(origin is not None, f"Module {mod} has no __file__")
    origin_path = Path(origin).resolve()
    require(
        origin_path == standalone_root or origin_path.is_relative_to(standalone_root),
        f"Escape detected: {mod} origin {origin_path} is outside {standalone_root}"
    )

# Verify negative boundary: importing runtime modules must fail due to genuine absence
def missing_is_requested_module_or_parent(requested: str, missing: str | None) -> bool:
    if not missing:
        return False
    return missing == requested or requested.startswith(missing + ".")

for forbidden in ('app.main', 'app.agent_router', 'app.models.database', 'app.services.orchestrator'):
    try:
        __import__(forbidden)
    except ModuleNotFoundError as exc:
        require(
            missing_is_requested_module_or_parent(forbidden, exc.name),
            f"{forbidden} exists but failed transitively because {exc.name} is missing",
        )
    else:
        require(False, f"Forbidden module {forbidden} unexpectedly imported!")

print("ALL_CORE_PROBES_PASSED")
"""
    result = subprocess.run(
        [python_executable, "-c", probe_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Core probe failed with stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "ALL_CORE_PROBES_PASSED" in result.stdout


def _run_v1_probe(standalone: Path, python_executable: str = sys.executable) -> None:
    v1_code = """
from typing import get_args
from app.agent_review.schemas import (
    FinalReviewVerdict,
    ReviewQualityGateStatus,
)
from app.agent_review.semantic_chunker import classify_file

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

# Exercise v1 deterministic classification and quality gate verdicts
require(classify_file("app/main.py") == "primary_backend_logic", "Failed classifying app/main.py")
require(classify_file("tests/test_foo.py") == "tests", "Failed classifying tests/test_foo.py")
require(classify_file("docs/readme.md") == "docs_changelog", "Failed classifying docs/readme.md")
require("passed" in get_args(ReviewQualityGateStatus), "ReviewQualityGateStatus missing passed")
require("approved" in get_args(FinalReviewVerdict), "FinalReviewVerdict missing approved")
print("V1_POSITIVE_CONTROL_PASSED")
"""
    result = subprocess.run(
        [python_executable, "-c", v1_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"v1 probe failed with stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "V1_POSITIVE_CONTROL_PASSED" in result.stdout


def _run_v2_probe(standalone: Path, head_sha: str, python_executable: str = sys.executable) -> None:
    v2_code = f"""
from pathlib import Path
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2
from app.agent_review.schema_export_v2 import render_v2_json_schemas

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

# 1. Target pack manifest build requires Git tree traversal of templates/ and schemas/
manifest = build_target_pack_manifest_v2(
    toolrepo_root=Path('.'),
    toolrepo_sha='{head_sha}',
    pack_version='v2.0.0-standalone',
)
require(manifest.pack_version == 'v2.0.0-standalone', "Unexpected pack version")
require(len(manifest.generated_files) > 0, "No generated files in manifest")
require(len(manifest.schema_digests) == 22, f"Expected 22 schema digests, got {{len(manifest.schema_digests)}}")

# 2. Schema export produces all 22 schemas matching the standalone schema directory
rendered = render_v2_json_schemas()
require(len(rendered) == 22, "Rendered schemas count != 22")
for schema_name, schema_dict in rendered.items():
    schema_file = Path('schemas/agent-review/v2') / schema_name
    require(schema_file.is_file(), f"Missing schema on disk: {{schema_name}}")

print("V2_POSITIVE_CONTROL_PASSED")
"""
    result = subprocess.run(
        [python_executable, "-c", v2_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"v2 probe failed with stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "V2_POSITIVE_CONTROL_PASSED" in result.stdout


def _run_coexistence_probe(standalone: Path, head_sha: str, python_executable: str = sys.executable) -> None:
    coexist_code = f"""
from pathlib import Path
from typing import get_args

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

# v1 import and execution
from app.agent_review.schemas import FinalReviewVerdict
from app.agent_review.semantic_chunker import classify_file
require(classify_file("app/main.py") == "primary_backend_logic", "Failed classifying app/main.py")
require("approved" in get_args(FinalReviewVerdict), "FinalReviewVerdict missing approved")

# v2 import and execution
from app.agent_review.target_pack_build_v2 import build_target_pack_manifest_v2
manifest = build_target_pack_manifest_v2(
    toolrepo_root=Path('.'),
    toolrepo_sha='{head_sha}',
    pack_version='v2.0.0-coexistence',
)
require(len(manifest.schema_digests) == 22, "Expected 22 schema digests")
print("COEXISTENCE_PASSED")
"""
    result = subprocess.run(
        [python_executable, "-c", coexist_code],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"coexistence probe failed with stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "COEXISTENCE_PASSED" in result.stdout


def test_positive_control_v1_offline_execution(tmp_path: Path) -> None:
    """B0-C6: v1 representative offline operation executes cleanly in standalone environment."""
    standalone = tmp_path / "standalone_v1"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    _run_v1_probe(standalone)


def test_positive_control_v2_external_asset_traversal(tmp_path: Path) -> None:
    """B0-C4, B0-C6: v2 target-pack build traverses external templates and schemas in standalone root."""
    standalone = tmp_path / "standalone_v2"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    head_sha = _init_git_in_standalone(standalone)
    _run_v2_probe(standalone, head_sha)


def test_positive_control_v1_v2_coexistence(tmp_path: Path) -> None:
    """B0-C6: v1 and v2 coexist in the exact same standalone materialization without conflict."""
    standalone = tmp_path / "standalone_coexistence"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    head_sha = _init_git_in_standalone(standalone)
    _run_coexistence_probe(standalone, head_sha)


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


def test_countermodel_m4_inject_runtime_only_dependency(tmp_path: Path) -> None:
    """Countermodel M4: Injecting an AIOps-only runtime dependency into declared distribution source causes validator to FAIL."""
    manifest = validator.load_manifest()

    # Case A: Inject forbidden runtime surface into distribution_boundary declaration
    mutated_surfaces = copy.deepcopy(manifest)
    mutated_surfaces["distribution_boundary"]["core_packages"].append("app/models/database.py")
    errors_a = validator.validate_manifest(mutated_surfaces, repo_root=REPO_ROOT)
    assert any("Forbidden runtime surface" in err for err in errors_a)

    # Case B: Inject forbidden package import into actual source of a declared distribution file
    temp_repo = tmp_path / "temp_repo_m4"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)

    target_file = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_file.read_text(encoding="utf-8")
    target_file.write_text("import fastapi\n" + original_code, encoding="utf-8")

    errors_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime package 'fastapi'" in err for err in errors_b), (
        f"Expected forbidden package error for fastapi, got: {errors_b}"
    )


def test_countermodel_h2_forbidden_import_roots_contract(tmp_path: Path) -> None:
    """H2: Known forbidden runtime dependencies are checked by import root (not distribution name).

    Verifies that AST import scanning catches:
      - pydantic_settings (from distribution 'pydantic-settings')
      - duckduckgo_search (from distribution 'duckduckgo-search')
      - multipart (from distribution 'python-multipart')
      - fastapi (standard package)
      - sqlalchemy (standard package)
    And does not reject allowed packages such as pydantic.
    """
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_h2"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    target_file = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_file.read_text(encoding="utf-8")

    # Forbidden import roots must fail closed
    forbidden_cases = [
        ("pydantic_settings", "import pydantic_settings\n"),
        ("duckduckgo_search", "import duckduckgo_search\n"),
        ("multipart", "import multipart\n"),
        ("fastapi", "import fastapi\n"),
        ("sqlalchemy", "import sqlalchemy\n"),
    ]
    for root_name, injection in forbidden_cases:
        target_file.write_text(injection + original_code, encoding="utf-8")
        errors = validator.validate_manifest(manifest, repo_root=temp_repo)
        assert any(f"Forbidden runtime package '{root_name}'" in err for err in errors), (
            f"Expected forbidden runtime package error for {root_name}, got: {errors}"
        )

    # Allowed package import must NOT be rejected by known-negative gate
    target_file.write_text("import pydantic\n" + original_code, encoding="utf-8")
    errors_allowed = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("pydantic" in err for err in errors_allowed), (
        f"Allowed package 'pydantic' was rejected unexpectedly: {errors_allowed}"
    )


def test_countermodel_j2_leaked_runtime_module_with_transitive_dependency_failure(tmp_path: Path) -> None:
    """J2: Runtime module absence probe fails if forbidden module exists but fails transitively on missing dependency."""
    standalone = tmp_path / "standalone_j2"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    # Inject leaked app/main.py with missing transitive dependency into the materialized distribution
    leaked_main = standalone / "app" / "main.py"
    leaked_main.write_text("import definitely_missing_runtime_dependency\n", encoding="utf-8")

    probe_script = """
def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

def missing_is_requested_module_or_parent(requested: str, missing: str | None) -> bool:
    if not missing:
        return False
    return missing == requested or requested.startswith(missing + ".")

for forbidden in ('app.main', 'app.agent_router', 'app.models.database', 'app.services.orchestrator'):
    try:
        __import__(forbidden)
    except ModuleNotFoundError as exc:
        require(
            missing_is_requested_module_or_parent(forbidden, exc.name),
            f"{forbidden} exists but failed transitively because {exc.name} is missing",
        )
    else:
        require(False, f"Forbidden module {forbidden} unexpectedly imported!")

print("ALL_PASSED")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "app.main exists but failed transitively because definitely_missing_runtime_dependency is missing" in result.stderr


def test_countermodel_m5_missing_install_contract() -> None:
    """Countermodel M5: Required install contract artifacts cannot be omitted from the manifest, and declared files must exist."""
    manifest = validator.load_manifest()

    # Case A: Omit requirements-agent-review.lock from install_boundary
    mutated_omit_lock = copy.deepcopy(manifest)
    mutated_omit_lock["distribution_boundary"]["install_boundary"].remove(
        "requirements-agent-review.lock"
    )
    errors_lock = validator.validate_manifest(mutated_omit_lock, repo_root=REPO_ROOT)
    assert any("Required anchor(s) omitted from 'install_boundary'" in err for err in errors_lock)
    assert any("requirements-agent-review.lock" in err for err in errors_lock)

    # Case B: Completely empty install_boundary: []
    mutated_empty = copy.deepcopy(manifest)
    mutated_empty["distribution_boundary"]["install_boundary"] = []
    errors_empty = validator.validate_manifest(mutated_empty, repo_root=REPO_ROOT)
    assert any("cannot be empty" in err for err in errors_empty)

    # Case C: Declared artifact missing on filesystem (distinguishes omission from missing file)
    mutated_nonexistent = copy.deepcopy(manifest)
    mutated_nonexistent["distribution_boundary"]["install_boundary"].append("nonexistent-install-contract.lock")
    errors_nonexistent = validator.validate_manifest(mutated_nonexistent, repo_root=REPO_ROOT)
    assert any("does not exist" in err for err in errors_nonexistent)

    # Case D: Fail-closed on unsupported schema_id or manifest_version
    mutated_schema = copy.deepcopy(manifest)
    mutated_schema["schema_id"] = "agent-review.unsupported.schema.v99"
    errors_schema = validator.validate_manifest(mutated_schema, repo_root=REPO_ROOT)
    assert any("Unsupported or missing schema_id" in err for err in errors_schema)


def test_countermodel_t01_structural_non_vacuity_of_required_boundary_sections() -> None:
    """T-01: Manifest cannot omit or empty mandatory core, package roots, assets, CLIs, or install anchors."""
    manifest = validator.load_manifest()

    # 1. Remove core_packages section
    mutated_no_core = copy.deepcopy(manifest)
    del mutated_no_core["distribution_boundary"]["core_packages"]
    errs = validator.validate_manifest(mutated_no_core, repo_root=REPO_ROOT)
    assert any("Required boundary section 'core_packages' is missing" in err for err in errs)

    # 2. core_packages = []
    mutated_empty_core = copy.deepcopy(manifest)
    mutated_empty_core["distribution_boundary"]["core_packages"] = []
    errs = validator.validate_manifest(mutated_empty_core, repo_root=REPO_ROOT)
    assert any("Section 'core_packages' in distribution_boundary cannot be empty" in err for err in errs)

    # 3. Remove mandatory app/agent_review anchor from core_packages
    mutated_core_anchor = copy.deepcopy(manifest)
    mutated_core_anchor["distribution_boundary"]["core_packages"] = ["app/some_other_package"]
    errs = validator.validate_manifest(mutated_core_anchor, repo_root=REPO_ROOT)
    assert any("Required anchor(s) omitted from 'core_packages': ['app/agent_review']" in err for err in errs)

    # 4. package_roots = []
    mutated_empty_roots = copy.deepcopy(manifest)
    mutated_empty_roots["distribution_boundary"]["package_roots"] = []
    errs = validator.validate_manifest(mutated_empty_roots, repo_root=REPO_ROOT)
    assert any("Section 'package_roots' in distribution_boundary cannot be empty" in err for err in errs)

    # 5. required_asset_trees = []
    mutated_empty_assets = copy.deepcopy(manifest)
    mutated_empty_assets["distribution_boundary"]["required_asset_trees"] = []
    errs = validator.validate_manifest(mutated_empty_assets, repo_root=REPO_ROOT)
    assert any("Section 'required_asset_trees' in distribution_boundary cannot be empty" in err for err in errs)

    # 6. Remove one mandatory asset tree
    mutated_assets = copy.deepcopy(manifest)
    mutated_assets["distribution_boundary"]["required_asset_trees"].remove("schemas/agent-review/v2")
    errs = validator.validate_manifest(mutated_assets, repo_root=REPO_ROOT)
    assert any("Required anchor(s) omitted from 'required_asset_trees': ['schemas/agent-review/v2']" in err for err in errs)

    # 7. distribution_clis = []
    mutated_empty_clis = copy.deepcopy(manifest)
    mutated_empty_clis["distribution_boundary"]["distribution_clis"] = []
    errs = validator.validate_manifest(mutated_empty_clis, repo_root=REPO_ROOT)
    assert any("Section 'distribution_clis' in distribution_boundary cannot be empty" in err for err in errs)

    # 8. Remove required CLI anchor (e.g. scripts/agent-review-target-pack-v2.py)
    mutated_clis = copy.deepcopy(manifest)
    mutated_clis["distribution_boundary"]["distribution_clis"].remove("scripts/agent-review-target-pack-v2.py")
    errs = validator.validate_manifest(mutated_clis, repo_root=REPO_ROOT)
    assert any("Required anchor(s) omitted from 'distribution_clis': ['scripts/agent-review-target-pack-v2.py']" in err for err in errs)

    # 9. Invariant: Omission from manifest is distinguished from declared-but-missing on disk
    mutated_missing_disk = copy.deepcopy(manifest)
    mutated_missing_disk["distribution_boundary"]["distribution_clis"].append("scripts/nonexistent-cli-anchor.py")
    errs_disk = validator.validate_manifest(mutated_missing_disk, repo_root=REPO_ROOT)
    assert any("Declared distribution path does not exist: scripts/nonexistent-cli-anchor.py" in err for err in errs_disk)
    assert not any("omitted from 'distribution_clis'" in err for err in errs_disk)


def test_countermodel_t02_import_from_submodule_escape_and_star_import(tmp_path: Path) -> None:
    """T-02: ImportFrom cannot bypass forbidden module checks via allowed parent; internal star imports fail closed."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_t02"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    target_cli = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_cli.read_text(encoding="utf-8")

    # Case A: from app import models -> MUST FAIL (forbidden app.models)
    target_cli.write_text("from app import models\n" + original_code, encoding="utf-8")
    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models' imported" in err for err in errs_a)

    # Case B: from app.services import orchestrator -> MUST FAIL (forbidden app.services.orchestrator)
    target_cli.write_text("from app.services import orchestrator\n" + original_code, encoding="utf-8")
    errs_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.services.orchestrator' imported" in err for err in errs_b)

    # Case C: from app.agent_review import contracts_v2 -> MUST PASS
    target_cli.write_text("from app.agent_review import contracts_v2\n" + original_code, encoding="utf-8")
    errs_c = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("contracts_v2" in err for err in errs_c)

    # Case D: from app.common import strict_json -> MUST PASS
    target_cli.write_text("from app.common import strict_json\n" + original_code, encoding="utf-8")
    errs_d = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("strict_json" in err for err in errs_d)

    # Case E: from app.common.strict_json import strict_json_loads -> MUST PASS (symbol, not module)
    target_cli.write_text("from app.common.strict_json import strict_json_loads\n" + original_code, encoding="utf-8")
    errs_e = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("strict_json_loads" in err for err in errs_e)

    # Case F: from app import * -> MUST FAIL (ambiguous internal star import fail-closed)
    target_cli.write_text("from app import *\n" + original_code, encoding="utf-8")
    errs_f = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Ambiguous internal star import 'app.*'" in err for err in errs_f)


def test_countermodel_t03_declared_path_confinement_and_symlink_escape(tmp_path: Path) -> None:
    """T-03: Paths must be canonical POSIX repository-relative without parent traversal, absolute paths, or symlink escapes."""
    manifest = validator.load_manifest()

    # Case A: Parent traversal ../
    mutated_parent = copy.deepcopy(manifest)
    mutated_parent["distribution_boundary"]["core_packages"].append("../other_dir")
    errs_a = validator.validate_manifest(mutated_parent, repo_root=REPO_ROOT)
    assert any("Parent-traversal '..' component not permitted" in err for err in errs_a)

    # Case B: Absolute path
    mutated_abs = copy.deepcopy(manifest)
    mutated_abs["distribution_boundary"]["core_packages"].append("/etc/passwd")
    errs_b = validator.validate_manifest(mutated_abs, repo_root=REPO_ROOT)
    assert any("Absolute paths not permitted" in err for err in errs_b)

    # Case C: Nested parent traversal attempting escape to forbidden runtime surface
    mutated_nested = copy.deepcopy(manifest)
    mutated_nested["distribution_boundary"]["core_packages"].append("app/agent_review/../models/__init__.py")
    errs_c = validator.validate_manifest(mutated_nested, repo_root=REPO_ROOT)
    assert any("Parent-traversal '..' component not permitted" in err for err in errs_c)

    # Case D: Source symlink escaping repository root
    temp_repo = tmp_path / "temp_repo_t03"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("secret outside repo", encoding="utf-8")
    symlink_file = temp_repo / "app" / "agent_review" / "escaping_symlink.py"
    symlink_file.symlink_to(outside_file)

    errs_d = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Symlink found" in err or "escapes repository root" in err for err in errs_d)


def test_countermodel_t04_explicit_empty_manifest_fallback(tmp_path: Path) -> None:
    """T-04: Explicit empty manifest is validated and rejected, never silently falling back to default."""
    target = tmp_path / "target_t04"

    # Case A: manifest={} raises StandaloneClosureValidationError
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_info:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target, manifest={})
    assert "Unsupported or missing schema_id" in str(exc_info.value) or "missing required 'distribution_boundary'" in str(exc_info.value)

    # Case B: manifest={"distribution_boundary": {}} raises StandaloneClosureValidationError
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_info_b:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target, manifest={"distribution_boundary": {}})
    assert "Unsupported or missing schema_id" in str(exc_info_b.value)

    # Case C: manifest=None uses repository default and succeeds
    target_c = tmp_path / "target_t04_default"
    dest = validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target_c, manifest=None)
    assert dest.exists()
    assert (dest / "app" / "agent_review").is_dir()


def test_countermodel_t05_clean_materialization_target_and_output_closure(tmp_path: Path) -> None:
    """T-05: Nonempty target is rejected without deleting existing files; output closure is enforced."""
    manifest = validator.load_manifest()

    # Case A: Absent target directory is created and populated
    target_absent = tmp_path / "target_absent"
    assert not target_absent.exists()
    dest_a = validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target_absent)
    assert dest_a.is_dir()

    # Case B: Existing empty target directory is accepted
    target_empty = tmp_path / "target_empty"
    target_empty.mkdir()
    dest_b = validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target_empty)
    assert dest_b.is_dir()

    # Case C: Existing nonempty target is rejected fail-closed; pre-seeded dirty file is NOT deleted
    target_dirty = tmp_path / "target_dirty"
    target_dirty.mkdir()
    dirty_marker = target_dirty / "app" / "models" / "database.py"
    dirty_marker.parent.mkdir(parents=True)
    dirty_marker.write_text("# preexisting dirty runtime file", encoding="utf-8")

    with pytest.raises(validator.StandaloneClosureValidationError) as exc_info_c:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=target_dirty, manifest=manifest)
    assert "Materialization target directory must be empty or absent" in str(exc_info_c.value)

    # Invariant: Materializer did NOT mutate or delete the caller's dirty file
    assert dirty_marker.exists()
    assert dirty_marker.read_text(encoding="utf-8") == "# preexisting dirty runtime file"


def test_countermodel_l1_noncanonical_alias_to_existing_materialization_target(tmp_path: Path) -> None:
    """L1: Noncanonical target (/tmp/new/../existing-target) is resolved before cleanliness check.

    Pre-existing nonempty target is rejected fail-closed, uncreated intermediate directory is NEVER created,
    and pre-existing target contents are never merged into or overwritten.
    """
    manifest = validator.load_manifest()

    existing_target = tmp_path / "existing-target"
    existing_target.mkdir()
    marker = existing_target / "existing_file.txt"
    marker.write_text("PREEXISTING_DATA", encoding="utf-8")

    uncreated_intermediate = tmp_path / "new"
    assert not uncreated_intermediate.exists()

    noncanonical_target = uncreated_intermediate / ".." / "existing-target"
    assert not noncanonical_target.exists(), "Precondition: raw noncanonical path does not exist because parent is absent"

    with pytest.raises(validator.StandaloneClosureValidationError) as exc_info:
        validator.materialize_standalone_distribution(
            repo_root=REPO_ROOT,
            target_dir=noncanonical_target,
            manifest=manifest,
        )

    assert "Materialization target directory must be empty or absent" in str(exc_info.value)
    assert not uncreated_intermediate.exists(), "Uncreated intermediate directory must NOT have been created"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "PREEXISTING_DATA"
    assert not (existing_target / "app").exists(), "Distribution files must not be copied into pre-existing target"

    # Positive control: noncanonical path pointing to a fresh, absent target succeeds and returns resolved target
    fresh_target = uncreated_intermediate / ".." / "fresh-target"
    assert not (tmp_path / "fresh-target").exists()
    dest = validator.materialize_standalone_distribution(
        repo_root=REPO_ROOT,
        target_dir=fresh_target,
        manifest=manifest,
    )
    assert dest == (tmp_path / "fresh-target").resolve()
    assert dest.is_dir()
    assert (dest / "app" / "agent_review").is_dir()


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


def test_countermodel_u01_relative_import_from_submodule_escape(tmp_path: Path) -> None:
    """U-01: Relative ImportFrom nodes resolve against their package hierarchy and block leaks to forbidden surfaces."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_u01"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    target_cli = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_cli.read_text(encoding="utf-8")

    # Case A: from .. import models -> MUST FAIL (relative import into forbidden app.models)
    target_cli.write_text("from .. import models\n" + original_code, encoding="utf-8")
    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models' imported" in err for err in errs_a)

    # Case B: from ..models import database -> MUST FAIL (forbidden app.models)
    target_cli.write_text("from ..models import database\n" + original_code, encoding="utf-8")
    errs_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models" in err for err in errs_b)

    # Case C: from ... import beyond_root -> MUST FAIL (escapes package root)
    target_cli.write_text("from ... import beyond_root\n" + original_code, encoding="utf-8")
    errs_c = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Relative import with level 3 escapes top-level package" in err for err in errs_c)

    # Case D: Positive control - legitimate relative import within package
    target_cli.write_text("from . import contracts_v2\n" + original_code, encoding="utf-8")
    errs_d = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("contracts_v2" in err for err in errs_d)

    # Case E: Positive control - legitimate relative import of shared primitive
    target_cli.write_text("from ..common import strict_json\n" + original_code, encoding="utf-8")
    errs_e = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("strict_json" in err for err in errs_e)


def test_countermodel_u02_component_prefix_boundary_enforcement(tmp_path: Path) -> None:
    """U-02: Component prefix boundary enforces exact package boundaries (e.g. app.agent_review vs app.agent_review_runtime)."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_u02"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    target_cli = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_cli.read_text(encoding="utf-8")

    # Case A: Sibling package starting with app.agent_review prefix -> MUST FAIL
    target_cli.write_text("import app.agent_review_runtime\n" + original_code, encoding="utf-8")
    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Local module 'app.agent_review_runtime' imported" in err and "outside declared boundary" in err for err in errs_a)

    # Case B: Sibling package under app.common that is not declared -> MUST FAIL
    target_cli.write_text("import app.common.extra_helper\n" + original_code, encoding="utf-8")
    errs_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Local module 'app.common.extra_helper' imported" in err and "outside declared boundary" in err for err in errs_b)

    # Case C: Positive control - exact allowed package and submodule
    target_cli.write_text("import app.agent_review\nimport app.agent_review.cli\n" + original_code, encoding="utf-8")
    errs_c = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("outside declared boundary" in err for err in errs_c)


def test_countermodel_u03_symlink_leaving_declared_subtree(tmp_path: Path) -> None:
    """U-03: Symlinks inside declared trees must not leave their declared source subtree or point to forbidden surfaces."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_u03"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)

    # Case A: Internal symlink pointing to an undeclared repository directory
    forbidden_target = temp_repo / "app" / "models"
    forbidden_target.mkdir(parents=True, exist_ok=True)
    (forbidden_target / "leak.py").write_text("# leak", encoding="utf-8")

    symlink_dir = temp_repo / "app" / "agent_review" / "leak_dir"
    symlink_dir.symlink_to(forbidden_target)

    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Symlink found" in err or "leaves declared subtree" in err or "resolves to forbidden surface" in err for err in errs_a)

    # Invariant: materialize_standalone_distribution rejects it fail-closed
    target_out = tmp_path / "target_out_u03"
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_info:
        validator.materialize_standalone_distribution(repo_root=temp_repo, target_dir=target_out, manifest=manifest)
    assert any(
        msg in str(exc_info.value)
        for msg in (
            "Symlink found in declared distribution tree",
            "Refusing to materialize distribution tree containing symlink",
            "leaves declared subtree",
            "symlink to forbidden surface",
        )
    )


def test_countermodel_u04_target_overlapping_declared_source_paths(tmp_path: Path) -> None:
    """U-04: Materialization target must be disjoint from every declared source path before creation."""
    # Case A: Target nested inside a declared source directory
    nested_target = REPO_ROOT / "app" / "agent_review" / "nested_target_probe"
    assert not nested_target.exists()

    with pytest.raises(validator.StandaloneClosureValidationError) as exc_a:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=nested_target)
    assert "Target directory is nested inside declared source path" in str(exc_a.value)
    # Critical invariant: Target directory must NOT have been created on disk
    assert not nested_target.exists(), "Target directory was created before disjointness check!"

    # Case B: Target equals a declared source path
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_b:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=REPO_ROOT / "app" / "agent_review")
    assert "Target directory overlaps declared source path" in str(exc_b.value)

    # Case C: Target is an ancestor of declared source paths (e.g. repo_root itself)
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_c:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=REPO_ROOT)
    assert "Declared source path is nested inside target directory" in str(exc_c.value)


def test_countermodel_u05_bidirectional_forbidden_surface_overlap() -> None:
    """U-05: Bidirectional overlap check rejects declared paths that are ancestors, descendants, or equal to forbidden surfaces."""
    manifest = validator.load_manifest()

    # Case A: Declaring an ancestor directory of a forbidden surface (e.g. "config" which contains forbidden "config/actions.yaml")
    mutated_ancestor = copy.deepcopy(manifest)
    mutated_ancestor["distribution_boundary"]["core_packages"].append("config")
    errs_a = validator.validate_manifest(mutated_ancestor, repo_root=REPO_ROOT)
    assert any("Forbidden runtime surface declared in distribution_boundary: config overlaps config/actions.yaml" in err for err in errs_a)

    # Case B: Declaring "app" which is ancestor of "app/main.py", "app/models", etc.
    mutated_app = copy.deepcopy(manifest)
    mutated_app["distribution_boundary"]["core_packages"].append("app")
    errs_b = validator.validate_manifest(mutated_app, repo_root=REPO_ROOT)
    assert any("Forbidden runtime surface declared in distribution_boundary: app overlaps" in err for err in errs_b)

    # Case C: Sibling directory path sharing prefix without component boundary does NOT falsely overlap
    assert not validator.paths_overlap("app/models_extra", "app/models")
    assert not validator.paths_overlap("app/services_extra", "app/services/orchestrator.py")


def test_countermodel_r01_negative_boundary_non_vacuity() -> None:
    """R-01: Negative boundary cannot be omitted, empty, or missing required runtime surface anchors."""
    manifest = validator.load_manifest()

    # Case A: Missing forbidden_runtime_surfaces section
    mutated_a = copy.deepcopy(manifest)
    del mutated_a["forbidden_runtime_surfaces"]
    errs_a = validator.validate_manifest(mutated_a, repo_root=REPO_ROOT)
    assert any("forbidden_runtime_surfaces" in err for err in errs_a)

    # Case B: Empty forbidden_runtime_surfaces list
    mutated_b = copy.deepcopy(manifest)
    mutated_b["forbidden_runtime_surfaces"] = []
    errs_b = validator.validate_manifest(mutated_b, repo_root=REPO_ROOT)
    assert any("cannot be empty" in err for err in errs_b)

    # Case C: Removal of a mandatory negative anchor (e.g. config/actions.yaml)
    mutated_c = copy.deepcopy(manifest)
    mutated_c["forbidden_runtime_surfaces"].remove("config/actions.yaml")
    errs_c = validator.validate_manifest(mutated_c, repo_root=REPO_ROOT)
    assert any("Required negative runtime anchor(s) omitted" in err and "config/actions.yaml" in err for err in errs_c)

    # Case D: Positive control - declaring a forbidden surface in positive boundary fails overlap check
    mutated_d = copy.deepcopy(manifest)
    mutated_d["distribution_boundary"]["core_packages"].append("app/main.py")
    errs_d = validator.validate_manifest(mutated_d, repo_root=REPO_ROOT)
    assert any("Forbidden runtime surface declared in distribution_boundary: app/main.py" in err for err in errs_d)


def test_countermodel_r02_relative_target_and_symlinked_target(tmp_path: Path) -> None:
    """R-02: Target destination confinement functions correctly with relative targets and rejects symlinked targets fail-closed."""
    # Case A: Relative target path materializes cleanly without spurious escaping errors
    rel_target_str = os.path.relpath(tmp_path / "relative_target", Path.cwd())
    rel_target = Path(rel_target_str)
    assert not rel_target.is_absolute()
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=rel_target)
    assert (rel_target / "app" / "agent_review" / "contracts_v2.py").is_file()

    # Case B: Symlinked target directory fails closed
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    symlink_target = tmp_path / "symlink_target"
    symlink_target.symlink_to(real_dir)
    with pytest.raises(validator.StandaloneClosureValidationError) as exc_b:
        validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=symlink_target)
    assert "Materialization target directory cannot be a symlink" in str(exc_b.value)


def test_countermodel_r03_symlink_cycles_and_internal_symlinks(tmp_path: Path) -> None:
    """R-03: Cyclic symlinks and broken internal symlinks within declared distribution members fail closed."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_r03"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    # Ensure forbidden surfaces exist in temp_repo so manifest validation satisfies non-vacuity check
    for forbidden in manifest["forbidden_runtime_surfaces"]:
        p = temp_repo / forbidden
        p.parent.mkdir(parents=True, exist_ok=True)
        if "." in p.name:
            p.write_text("# dummy\n", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)

    # Case A: Cyclic symlink inside declared tree
    cycle_dir = temp_repo / "app" / "agent_review" / "sub_cycle"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    (cycle_dir / "loop").symlink_to(cycle_dir)

    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Symlink found in declared distribution tree" in err for err in errs_a)

    with pytest.raises(validator.StandaloneClosureValidationError) as exc_a:
        validator.materialize_standalone_distribution(repo_root=temp_repo, target_dir=tmp_path / "out_cycle", manifest=manifest)
    assert any(
        msg in str(exc_a.value)
        for msg in (
            "Symlink found in declared distribution tree",
            "Refusing to materialize distribution tree containing symlink",
        )
    )

    # Clean up cycle_dir so Case B can run independently
    (cycle_dir / "loop").unlink()
    cycle_dir.rmdir()

    # Case B: Broken internal symlink pointing to nonexistent target
    broken_link = temp_repo / "app" / "agent_review" / "broken_link.py"
    broken_link.symlink_to(temp_repo / "app" / "agent_review" / "nonexistent_target.py")

    errs_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Symlink found in declared distribution tree" in err for err in errs_b)

    with pytest.raises(validator.StandaloneClosureValidationError) as exc_b:
        validator.materialize_standalone_distribution(repo_root=temp_repo, target_dir=tmp_path / "out_broken", manifest=manifest)
    assert any(
        msg in str(exc_b.value)
        for msg in (
            "Symlink found in declared distribution tree",
            "Refusing to materialize distribution tree containing symlink",
        )
    )


def test_countermodel_f1_forbidden_packages_non_vacuity() -> None:
    """F-01: Negative forbidden package contract cannot be omitted, empty, or missing required anchors."""
    manifest = validator.load_manifest()

    # Case A: Missing dependency_closure dictionary
    mutated_a = copy.deepcopy(manifest)
    del mutated_a["dependency_closure"]
    errs_a = validator.validate_manifest(mutated_a, repo_root=REPO_ROOT)
    assert any("dependency_closure" in err for err in errs_a)

    # Case B: Missing forbidden_runtime_packages inside dependency_closure
    mutated_b = copy.deepcopy(manifest)
    del mutated_b["dependency_closure"]["forbidden_runtime_packages"]
    errs_b = validator.validate_manifest(mutated_b, repo_root=REPO_ROOT)
    assert any("forbidden_runtime_packages" in err for err in errs_b)

    # Case C: Empty forbidden_runtime_packages list
    mutated_c = copy.deepcopy(manifest)
    mutated_c["dependency_closure"]["forbidden_runtime_packages"] = []
    errs_c = validator.validate_manifest(mutated_c, repo_root=REPO_ROOT)
    assert any("cannot be empty" in err for err in errs_c)

    # Case D: Removal of a mandatory negative anchor (e.g. fastapi)
    mutated_d = copy.deepcopy(manifest)
    mutated_d["dependency_closure"]["forbidden_runtime_packages"] = [
        p for p in mutated_d["dependency_closure"]["forbidden_runtime_packages"] if p.lower() != "fastapi"
    ]
    errs_d = validator.validate_manifest(mutated_d, repo_root=REPO_ROOT)
    assert any("Required negative runtime package anchor(s) omitted" in err and "fastapi" in err for err in errs_d)


def test_countermodel_f3_import_semantics_exact_or_descendant(tmp_path: Path) -> None:
    """F-03: Python module forbidden matching uses exact-or-descendant semantics, separating module imports from disk path overlap."""
    manifest = validator.load_manifest()
    temp_repo = tmp_path / "temp_repo_f3"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=temp_repo)
    # Ensure forbidden surfaces exist in temp_repo so manifest validation satisfies non-vacuity check
    for forbidden in manifest["forbidden_runtime_surfaces"]:
        p = temp_repo / forbidden
        p.parent.mkdir(parents=True, exist_ok=True)
        if "." in p.name:
            p.write_text("# dummy\n", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)

    target_cli = temp_repo / "app" / "agent_review" / "cli.py"
    original_code = target_cli.read_text(encoding="utf-8")

    # Case A: Harmless parent package import: import app -> MUST PASS
    target_cli.write_text("import app\n" + original_code, encoding="utf-8")
    errs_a = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("Forbidden runtime module 'app'" in err for err in errs_a)

    # Case B: Harmless allowed package root import: import app.services -> MUST PASS
    target_cli.write_text("import app.services\n" + original_code, encoding="utf-8")
    errs_b = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("Forbidden runtime module 'app.services'" in err for err in errs_b)

    # Case C: Forbidden submodule reconstruction: from app import models -> MUST FAIL
    target_cli.write_text("from app import models\n" + original_code, encoding="utf-8")
    errs_c = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models'" in err for err in errs_c)

    # Case D: Direct forbidden import: import app.models -> MUST FAIL
    target_cli.write_text("import app.models\n" + original_code, encoding="utf-8")
    errs_d = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models'" in err for err in errs_d)

    # Case E: Forbidden deep descendant: import app.models.foo -> MUST FAIL
    target_cli.write_text("import app.models.foo\n" + original_code, encoding="utf-8")
    errs_e = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.models.foo'" in err for err in errs_e)

    # Case F: Forbidden submodule from allowed parent: from app.services import orchestrator -> MUST FAIL
    target_cli.write_text("from app.services import orchestrator\n" + original_code, encoding="utf-8")
    errs_f = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert any("Forbidden runtime module 'app.services.orchestrator'" in err for err in errs_f)

    # Case G: Positive control: legitimate import of core module -> MUST PASS
    target_cli.write_text("import app.agent_review.contracts_v2\n" + original_code, encoding="utf-8")
    errs_g = validator.validate_manifest(manifest, repo_root=temp_repo)
    assert not any("contracts_v2" in err for err in errs_g)


def test_countermodel_f4_optimization_resistance(tmp_path: Path) -> None:
    """F-04: Subprocess probes resist PYTHONOPTIMIZE and explicit require() calls cannot be eliminated."""
    # Case A: _clean_env strips PYTHONOPTIMIZE even if set in caller environment
    dirty_env = {"PYTHONOPTIMIZE": "2"}
    with pytest.MonkeyPatch.context() as mp:
        for k, v in dirty_env.items():
            mp.setenv(k, v)
        cleaned = _clean_env(tmp_path)
        assert "PYTHONOPTIMIZE" not in cleaned, "_clean_env must strip PYTHONOPTIMIZE!"

    # Case B: Probe with require() fails closed even under python -O (where assert would be omitted)
    standalone = tmp_path / "standalone_f4"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)

    failing_probe = """
def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

require(False, "Deliberate failure in probe")
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", failing_probe],
        cwd=standalone,
        env=_clean_env(standalone),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Probe with require(False) unexpectedly succeeded under python -O!"
    assert "Deliberate failure in probe" in result.stderr


def test_countermodel_f5_sibling_root_origin_confinement(tmp_path: Path) -> None:
    """F-05: Module origin confinement uses resolved path components (is_relative_to), not raw string prefix."""
    standalone_root = tmp_path / "standalone"
    standalone_root.mkdir()
    sibling_root = tmp_path / "standalone-old"
    sibling_root.mkdir()
    sibling_file = sibling_root / "app" / "contracts_v2.py"

    resolved_root = standalone_root.resolve()
    resolved_file = sibling_file.resolve()

    # Flaw proof: raw startswith falsely accepts the sibling root because "/tmp/standalone-old".startswith("/tmp/standalone") is True
    assert str(resolved_file).startswith(str(resolved_root)), "Precondition: sibling file path must start with root string"

    # Fix proof: resolved path component confinement correctly rejects the sibling root
    is_contained = (resolved_file == resolved_root) or resolved_file.is_relative_to(resolved_root)
    assert not is_contained, "Component-aware is_relative_to must reject sibling directory path!"


@pytest.mark.requires_network
def test_lock_built_venv_executes_materialized_standalone_agentreview(tmp_path: Path) -> None:
    """F-02 (Layer E x Layer I Composed Gate): Materialized AgentReview executes under interpreter built from requirements-agent-review.lock."""
    standalone = tmp_path / "standalone_composed"
    validator.materialize_standalone_distribution(repo_root=REPO_ROOT, target_dir=standalone)
    head_sha = _init_git_in_standalone(standalone)

    venv_dir = tmp_path / "venv"
    install_script = standalone / "scripts" / "install-agent-review-toolrepo.sh"
    env = os.environ.copy()
    if "AGENT_REVIEW_PYTHON" not in env:
        py311 = shutil.which("python3.11")
        if py311:
            env["AGENT_REVIEW_PYTHON"] = py311

    install_result = subprocess.run(
        ["bash", str(install_script), str(venv_dir)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert install_result.returncode == 0, f"Installer failed with returncode {install_result.returncode}:\n{install_result.stderr}\n{install_result.stdout}"

    venv_python = str(venv_dir / "bin" / "python3")
    assert Path(venv_python).is_file(), f"Venv python binary not found at {venv_python}"

    # Execute representative Layer E probes with the lock-built interpreter
    _run_core_probe(standalone, python_executable=venv_python)
    _run_v1_probe(standalone, python_executable=venv_python)
    _run_v2_probe(standalone, head_sha, python_executable=venv_python)
    _run_coexistence_probe(standalone, head_sha, python_executable=venv_python)
