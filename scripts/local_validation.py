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
MAX_COLLECTION_BYTES = 16 * 1024 * 1024


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


def read_collection(path: Path) -> dict:
    """Bounded evidence read; missing/unreadable never means an empty corpus."""
    unknown = {'collection_count': None, 'collection_sha256': None, 'ids': None}
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_COLLECTION_BYTES + 1)
        if len(raw) > MAX_COLLECTION_BYTES:
            raise ValueError('collection report exceeds size bound')
        ids = json.loads(raw.decode('utf-8'))
        if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError('collection must be a list of nonempty node IDs')
        if len(set(ids)) != len(ids):
            raise ValueError('collection contains duplicate node IDs')
    except FileNotFoundError:
        return {**unknown, 'collection_state': 'missing'}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {**unknown, 'collection_state': 'unreadable'}
    return {'collection_state': 'valid', 'collection_count': len(ids),
            'collection_sha256': hashlib.sha256(raw).hexdigest(), 'ids': ids}


def write_receipt(path: Path, data: dict) -> None:
    """Replace the last lane receipt atomically, without truncating it in place."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    collection = read_collection(directory / f'{name}-collection.json')
    data.update({key: value for key, value in collection.items() if key != 'ids'})
    if not timed_out and (collection['collection_state'] == 'unreadable' or
            (code == 0 and name in ('ordinary-collection', 'parallel', 'serial', 'network') and
             collection['collection_state'] != 'valid')):
        data['status'] = 'INCOMPLETE_TEST_REPORT'
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
        if data['status'] == 'PASSED':
            reports = {lane: read_collection(directory / f'{lane}-collection.json')
                       for lane in ('ordinary-collection', 'parallel', 'serial')}
            if any(report['collection_state'] != 'valid' for report in reports.values()):
                data['status'] = 'INCOMPLETE_TEST_REPORT'
            else:
                ids = {lane: set(report['ids']) for lane, report in reports.items()}
                equivalent = (ids['ordinary-collection'] == ids['parallel'] | ids['serial'] and
                              not ids['parallel'] & ids['serial'])
                data['collection_equivalence'] = equivalent
                if not equivalent:
                    data['status'] = 'COLLECTION_MISMATCH'
        write_receipt(receipt, data)
        if result['status'] != 'PASSED' or data['status'] in ('SUBJECT_MOVED', 'INCOMPLETE_TEST_REPORT', 'COLLECTION_MISMATCH'):
            break
    print(f'[local-validation] {data["status"]}; receipt={receipt}', flush=True)
    return 0 if data['status'] == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
