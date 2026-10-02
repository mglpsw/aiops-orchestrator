#!/usr/bin/env python3
"""Offline full validation receipt. This is evidence, never merge authority."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from scripts.test_workers import characterize

ROOT = Path(__file__).resolve().parent.parent


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def identity() -> dict:
    status = git('status', '--porcelain')
    diff = subprocess.check_output(['git', 'diff', 'HEAD', '--binary'], cwd=ROOT)
    return {'head_sha': git('rev-parse', 'HEAD'), 'tree_sha': git('rev-parse', 'HEAD^{tree}'),
            'base_ref_sha': git('rev-parse', 'origin/master'),
            'dirty': bool(status), 'status_digest': hashlib.sha256(status.encode()).hexdigest(),
            'tracked_diff_digest': hashlib.sha256(diff).hexdigest()}


def test_counts(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        cases = ET.parse(path).getroot().findall('.//testcase')
    except ET.ParseError:
        return None  # Interrupted report is incomplete, never evidence of zero failures.
    return {'collected': len(cases), 'failed': sum(c.find('failure') is not None for c in cases),
            'errors': sum(c.find('error') is not None for c in cases),
            'skipped': sum(c.find('skipped') is not None for c in cases)}


def run_lane(name: str, command: list[str], directory: Path, timeout: int) -> dict:
    start, tick = utc(), time.monotonic()
    log = directory / f'{name}.log'
    timed_out = False
    with log.open('w') as output:
        env = {**os.environ, 'AIOPS_TEST_COLLECTION_REPORT': str(directory / f'{name}-collection.json')}
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    status = 'INCOMPLETE_TIMEOUT' if timed_out else 'PASSED' if code == 0 else 'SkippedByScope' if code == 5 else 'VALIDATION_FAILED'
    data = {'lane': name, 'commands': [command], 'start': start, 'end': utc(),
            'seconds': round(time.monotonic() - tick, 3), 'exit_status': code,
            'status': status, 'log': str(log), 'test_counts': test_counts(directory / f'{name}.xml')}
    collection = directory / f'{name}-collection.json'
    if collection.is_file():
        ids = json.loads(collection.read_text())
        data['collection_count'] = len(ids)
        data['collection_sha256'] = hashlib.sha256(collection.read_bytes()).hexdigest()
    print(json.dumps(data), flush=True)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, help='receipt path; default is a temporary directory')
    parser.add_argument('--lane-timeout', type=int, default=1800)
    args = parser.parse_args()
    if args.lane_timeout < 1:
        parser.error('lane timeout must be positive')
    if os.environ.get('PYTEST_ADDOPTS') or os.environ.get('AIOPS_INTEGRATION') == '1':
        parser.error('canonical offline full validation requires PYTEST_ADDOPTS unset and AIOPS_INTEGRATION != 1')
    os.chdir(ROOT)
    try:
        capacity = characterize()
    except ValueError as exc:
        parser.error(str(exc))
    directory = Path(tempfile.mkdtemp(prefix='aiops-validation-', dir=os.environ.get('RUNNER_TEMP')))
    receipt = args.receipt or directory / 'receipt.json'
    initial = identity()
    data = {'repository': 'mglpsw/aiops-orchestrator', 'base_sha': git('merge-base', 'HEAD', 'origin/master'),
            **initial, 'python_version': platform.python_version(), 'worker_count': capacity['selected_workers'],
            'capacity': capacity, 'start': utc(), 'lanes': [],
            'limitations': ['offline only; runtime/docker/integration tests excluded',
                            'local receipt is not a required GitHub check or a merge grant',
                            'every later push makes this receipt stale by default',
                            'serial lanes and parallel lane run sequentially; no shared-budget oversubscription']}
    specifications = [('static', ['bash', 'scripts/ci_validate.sh', '--static']),
                      ('ordinary-collection', ['bash', 'scripts/test.sh', '--serial', '--collect-only',
                                               '-p', 'scripts.test_census'])]
    for lane in ('parallel', 'serial', 'network'):
        command = ['bash', 'scripts/test.sh', '--lane', lane,
                   '-p', 'scripts.test_census',
                   f'--junitxml={directory / (lane + ".xml")}']
        if lane != 'parallel':
            command.append('--serial')
        specifications.append((lane, command))
    for name, command in specifications:
        result = run_lane(name, command, directory, args.lane_timeout)
        data['lanes'].append(result)
        data['end'] = utc()
        data['final_identity'] = identity()
        if initial != data['final_identity']:
            data['status'] = 'SUBJECT_MOVED'
        elif result['status'] != 'PASSED':
            data['status'] = result['status']
        elif name in ('parallel', 'serial', 'network') and (
                result.get('collection_count') is None or result['test_counts'] is None or
                result['test_counts']['collected'] != result['collection_count']):
            data['status'] = 'INCOMPLETE_TEST_REPORT'
        elif initial['dirty']:
            data['status'] = 'NOT_QUALIFIED_DIRTY_WORKTREE'
        else:
            data['status'] = 'IN_PROGRESS' if len(data['lanes']) < len(specifications) else 'PASSED'
        if len(data['lanes']) == len(specifications):
            ids = {lane: set(json.loads((directory / f'{lane}-collection.json').read_text()))
                   for lane in ('ordinary-collection', 'parallel', 'serial')}
            equivalent = (ids['ordinary-collection'] == ids['parallel'] | ids['serial'] and
                          not ids['parallel'] & ids['serial'])
            data['collection_equivalence'] = equivalent
            if not equivalent:
                data['status'] = 'COLLECTION_MISMATCH'
        receipt.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
        if result['status'] != 'PASSED' or data['status'] in ('SUBJECT_MOVED', 'INCOMPLETE_TEST_REPORT', 'COLLECTION_MISMATCH'):
            break
    print(f'[local-validation] {data["status"]}; receipt={receipt}', flush=True)
    return 0 if data['status'] == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
