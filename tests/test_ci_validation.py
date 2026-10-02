"""Prove partition coverage and runner failures using a tiny independent corpus."""
from pathlib import Path
import shutil
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import pytest
import yaml
from scripts import test_runner as runner
from scripts.local_validation import run_lane, test_counts as counts
from scripts.test_lanes import LANES
from scripts.test_workers import characterize

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def miniature(tmp_path, monkeypatch):
    monkeypatch.delenv('AIOPS_INTEGRATION', raising=False)
    monkeypatch.setenv('AIOPS_TEST_WORKERS', str(min(2, characterize()['selected_workers'])))
    for name in ('test.sh', 'test_runner.py', 'test_workers.py', 'test_lanes.py', 'test_census.py'):
        dest = tmp_path / 'scripts' / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'scripts' / name, dest)
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'pytest.ini').write_text('[pytest]\nmarkers =\n serial_required: serial\n requires_network: subprocess\n integration: external\n requires_runtime: external\n requires_docker: external\n requires_prometheus: external\n')
    (tmp_path / 'tests/test_corpus.py').write_text('''import pytest
def test_parallel(): assert True
@pytest.mark.serial_required
def test_serial(): assert True
@pytest.mark.requires_network
def test_network(): assert True
@pytest.mark.integration
def test_external(): raise AssertionError("external lane must not run")
''')
    # subprocess uses the same interpreter through test_runner, like test.sh.
    return tmp_path


def invoke(repo, *args):
    return subprocess.run([sys.executable, '-m', 'scripts.test_runner', *args], cwd=repo, text=True, capture_output=True)


def cases(path):
    return {case.attrib['name'] for case in ET.parse(path).getroot().findall('.//testcase')}


def test_partition_preserves_all_ordinary_and_keeps_network_separate(miniature):
    serial = miniature / 'ordinary.xml'
    result = invoke(miniature, '--serial', f'--junitxml={serial}')
    assert result.returncode == 0, result.stdout + result.stderr
    sets = {}
    for lane in LANES:
        report = miniature / f'{lane}.xml'
        collection = miniature / f'{lane}-ids.json'
        from unittest.mock import patch
        with patch.dict(os.environ, {'AIOPS_TEST_COLLECTION_REPORT': str(collection)}):
            result = invoke(miniature, '--lane', lane, '-p', 'scripts.test_census', f'--junitxml={report}')
        assert result.returncode == 0, result.stdout + result.stderr
        import json
        assert [nodeid.split('::')[-1] for nodeid in json.loads(collection.read_text())] == sorted(cases(report))
        sets[lane] = cases(report)
    assert sets['parallel'] == {'test_parallel'}
    assert sets['serial'] == {'test_serial'}
    assert sets['network'] == {'test_network'}
    assert sets['parallel'] | sets['serial'] == cases(serial)
    assert not (sets['parallel'] & sets['serial'])
    result = invoke(miniature)
    assert result.returncode == 0 and 'test_external' not in result.stdout


def test_failing_serial_exception_is_not_hidden(miniature):
    (miniature / 'tests/test_failure.py').write_text('import pytest\n@pytest.mark.serial_required\ndef test_bad(): assert False\n')
    result = invoke(miniature)
    assert result.returncode == 1 and 'test_bad' in result.stdout


def test_empty_lane_is_not_reported_as_passed(miniature):
    result = invoke(miniature, '--lane', 'parallel', '-k', 'does_not_exist')
    assert result.returncode == 5 and 'SkippedByScope' in result.stdout


def test_fallback_and_serial_never_use_xdist():
    for lane in LANES:
        for serial, xdist in ((True, True), (False, False)):
            command = runner.pytest_command(lane, serial=serial, workers=2, xdist=xdist, extra=[])
            assert '-n' not in command
    command = runner.pytest_command('parallel', serial=False, workers=2, xdist=True, extra=[])
    assert command[command.index('-n') + 1] == '2' and '--dist=loadfile' in command


@pytest.mark.parametrize('args', [('-m', 'requires_runtime'), ('-n', 'auto'), ('-n8',), ('-mrequires_runtime',), ('--lane', 'bad')])
def test_runner_rejects_partition_and_budget_bypass(miniature, args):
    assert invoke(miniature, *args).returncode == 2


def test_environment_cannot_override_partition(miniature, monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '-m requires_runtime')
    assert invoke(miniature).returncode == 2


def test_timeout_is_incomplete_not_test_failure(tmp_path):
    result = run_lane('probe', [sys.executable, '-c', 'import time; time.sleep(60)'], tmp_path, 1)
    assert result['status'] == 'INCOMPLETE_TIMEOUT' and result['exit_status'] == 124
    assert result['test_counts'] is None


def test_missing_or_truncated_report_does_not_invent_counts(tmp_path):
    report = tmp_path / 'probe.xml'
    assert counts(report) is None
    report.write_text('<testsuites>')
    assert counts(report) is None


def test_workflows_have_disjoint_owners_and_full_regression_remains_visible():
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    jobs = ci['jobs']
    commands = {name: '\n'.join(s.get('run', '') for s in job['steps']) for name, job in jobs.items()}
    assert 'ci_validate.sh --repository' in commands['validate']
    assert 'test_test_workers.py' in commands['validate']
    assert 'test_post_ready_codex_guard.py' in commands['validate']
    assert 'ci_validate.sh --generated' in commands['agent-review-release-gates']
    assert 'scripts/test.sh' not in commands['agent-review-release-gates']
    assert ci['permissions'] == {'contents': 'read'}
    full = yaml.safe_load((ROOT / '.github/workflows/full-regression.yml').read_text())
    # PyYAML uses YAML 1.1; GitHub's unquoted `on` key is parsed as True.
    triggers = full.get('on', full.get(True))
    assert set(triggers) == {'workflow_dispatch', 'schedule', 'pull_request'}
    patterns = triggers['pull_request']['paths']
    from fnmatch import fnmatchcase
    infrastructure = ['.github/workflows/ci.yml', '.github/workflows/full-regression.yml',
                      'scripts/ci_validate.sh', 'scripts/test.sh', 'scripts/test_runner.py',
                      'scripts/test_lanes.py', 'scripts/test_workers.py', 'scripts/test_census.py',
                      'scripts/local_validate.sh', 'scripts/local_validation.py', 'scripts/github_full_receipt.py',
                      'pytest.ini', 'requirements-dev.txt', 'tests/conftest.py',
                      'tests/test_ci_validation.py', 'tests/test_test_workers.py']
    assert all(any(fnmatchcase(path, pattern) for pattern in patterns) for path in infrastructure)
    ordinary = ['app/agent_review/pipeline.py', 'tests/agent_review/test_review_transport_v2.py',
                'tests/test_policy_engine.py', 'docs/TESTING.md']
    assert not any(fnmatchcase(path, pattern) for path in ordinary for pattern in patterns)
    assert full['permissions'] == {'contents': 'read'}
    steps = full['jobs']['regression']['steps']
    assert any('bash scripts/local_validate.sh' in s.get('run', '') for s in steps)
    assert any(s.get('if') == 'always()' and s.get('run') == 'python -m scripts.github_full_receipt' for s in steps)
    assert any(s.get('if') == 'always()' and s.get('uses', '').startswith('actions/upload-artifact@') for s in steps)


def test_invalid_ci_mode_cannot_execute_checks():
    result = subprocess.run(['bash', str(ROOT / 'scripts/ci_validate.sh'), '--unknown'], capture_output=True, text=True)
    assert result.returncode == 2 and '[static]' not in result.stdout


def test_github_receipt_distinguishes_source_and_synthetic_merge(tmp_path):
    from scripts.github_full_receipt import github_subject, enrich
    import json
    event = {'number': 371, 'pull_request': {'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}}}
    env = {'GITHUB_SHA': 'c' * 40, 'GITHUB_EVENT_NAME': 'pull_request',
           'GITHUB_REPOSITORY': 'mglpsw/aiops-orchestrator', 'GITHUB_RUN_ID': '123',
           'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_WORKFLOW_SHA': 'a' * 40,
           'GITHUB_WORKFLOW_REF': 'mglpsw/aiops-orchestrator/.github/workflows/full-regression.yml@refs/pull/371/merge'}
    def fake_git(*args):
        return 'c' * 40 if args == ('rev-parse', 'HEAD') else 'd' * 40
    subject = github_subject(event, env, fake_git)
    assert subject['head_sha'] == 'a' * 40 and subject['tested_synthetic_merge_sha'] == 'c' * 40
    assert subject['tree_equivalence'] and subject['pr'] == 371 and subject['run_attempt'] == 2
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'head_sha': 'c' * 40, 'tree_sha': 'd' * 40, 'status': 'VALIDATION_FAILED'}))
    (tmp_path / 'network.xml').write_text('<testsuites><testsuite><testcase classname="N" name="blocked"><skipped message="capability unavailable"/></testcase><testcase classname="N" name="broken"><failure message="failure preserved"/></testcase></testsuite></testsuites>')
    data = enrich(receipt, subject, '/usr/bin/sudo')
    assert data['status'] == 'VALIDATION_FAILED'  # Enrichment cannot promote a failed run.
    assert data['lane_results']['network'][0]['reason'] == 'capability unavailable'
    assert data['lane_results']['network'][1]['outcome'] == 'failure'
    assert data['environment_capabilities']['canonical_sudo_path'] == '/usr/bin/sudo'


def test_github_receipt_records_tree_difference_and_rejects_wrong_checkout(tmp_path):
    from scripts.github_full_receipt import github_subject, enrich
    import json
    event = {'number': 371, 'pull_request': {'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}}}
    env = {'GITHUB_SHA': 'c' * 40, 'GITHUB_EVENT_NAME': 'pull_request',
           'GITHUB_REPOSITORY': 'mglpsw/aiops-orchestrator', 'GITHUB_RUN_ID': '123',
           'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_WORKFLOW_SHA': 'a' * 40,
           'GITHUB_WORKFLOW_REF': 'mglpsw/aiops-orchestrator/.github/workflows/full-regression.yml@refs/pull/371/merge'}
    def fake_git(*args):
        if args == ('rev-parse', 'HEAD'): return 'c' * 40
        return 'd' * 40 if args[1].startswith('a') else 'e' * 40
    subject = github_subject(event, env, fake_git)
    assert not subject['tree_equivalence']
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'head_sha': 'a' * 40, 'tree_sha': 'd' * 40, 'status': 'PASSED'}))
    with pytest.raises(ValueError, match='identity differs'):
        enrich(receipt, subject, None)
    with pytest.raises(ValueError, match='GITHUB_SHA'):
        github_subject(event, {**env, 'GITHUB_SHA': 'f' * 40}, fake_git)
