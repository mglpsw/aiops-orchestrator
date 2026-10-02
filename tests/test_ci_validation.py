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
    assert set(triggers) == {'workflow_dispatch', 'schedule'}
    steps = full['jobs']['regression']['steps']
    assert any(s.get('run') == 'bash scripts/local_validate.sh' for s in steps)
    assert any(s.get('if') == 'always()' and s.get('uses', '').startswith('actions/upload-artifact@') for s in steps)


def test_invalid_ci_mode_cannot_execute_checks():
    result = subprocess.run(['bash', str(ROOT / 'scripts/ci_validate.sh'), '--unknown'], capture_output=True, text=True)
    assert result.returncode == 2 and '[static]' not in result.stdout
