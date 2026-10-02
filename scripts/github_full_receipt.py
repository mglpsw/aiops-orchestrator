#!/usr/bin/env python3
"""Add GitHub subject metadata to an existing full-validation artifact.

Evidence only: no API access, GitHub writes, v2 imports or status promotion.
PR body/diff content is never read as instructions.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def github_subject(event: dict, env: dict, git_output=git) -> dict:
    tested = git_output('rev-parse', 'HEAD')
    if tested != env['GITHUB_SHA']:
        raise ValueError('checkout differs from GITHUB_SHA')
    pr = event.get('pull_request') if env['GITHUB_EVENT_NAME'] == 'pull_request' else None
    source = pr['head']['sha'] if pr else tested
    base = pr['base']['sha'] if pr else git_output('merge-base', 'HEAD', 'origin/master')
    if any(re.fullmatch(r'[0-9a-f]{40}', sha) is None for sha in (source, base, tested)):
        raise ValueError('full Git commit identities expected')
    source_tree = git_output('rev-parse', f'{source}^{{tree}}')
    tested_tree = git_output('rev-parse', f'{tested}^{{tree}}')
    repository = env['GITHUB_REPOSITORY']
    workflow_ref = env['GITHUB_WORKFLOW_REF']
    prefix = repository + '/'
    if not workflow_ref.startswith(prefix):
        raise ValueError('workflow repository mismatch')
    return {'repository': repository, 'pr': event['number'] if pr else None,
            'event': env['GITHUB_EVENT_NAME'], 'base_sha': base, 'head_sha': source,
            'tested_sha': tested, 'tested_synthetic_merge_sha': tested if pr and tested != source else None,
            'source_tree': source_tree, 'tested_tree': tested_tree,
            'tree_equivalence': source_tree == tested_tree,
            'workflow_path': workflow_ref[len(prefix):].split('@', 1)[0],
            'workflow_ref': workflow_ref, 'workflow_sha': env['GITHUB_WORKFLOW_SHA'],
            'workflow_run_id': int(env['GITHUB_RUN_ID']), 'run_attempt': int(env['GITHUB_RUN_ATTEMPT'])}


def junit_results(path: Path) -> list[dict] | None:
    if not path.is_file():
        return None
    try:
        cases = ET.parse(path).getroot().findall('.//testcase')
    except ET.ParseError:
        return None
    results = []
    for case in cases:
        outcome, reason = 'passed', None
        for tag in ('skipped', 'failure', 'error'):
            element = case.find(tag)
            if element is not None:
                outcome, reason = tag, element.get('message')
                break
        results.append({'classname': case.get('classname'), 'name': case.get('name'),
                        'outcome': outcome, 'reason': reason})
    return results


def enrich(receipt: Path, subject: dict, sudo_path: str | None) -> dict:
    data = json.loads(receipt.read_text())
    # Generic local head/tree fields describe the checkout. The separate
    # GitHub envelope binds source HEAD and tested merge without conflating them.
    if data['head_sha'] != subject['tested_sha'] or data['tree_sha'] != subject['tested_tree']:
        raise ValueError('receipt checkout identity differs from GitHub subject')
    data['github_subject'] = subject
    data['environment_capabilities'] = {'canonical_sudo_path': sudo_path,
                                      'sudo_executable': sudo_path is not None,
                                      'usr_bin_sudo_executable': os.access('/usr/bin/sudo', os.X_OK)}
    data['lane_results'] = {lane: junit_results(receipt.parent / f'{lane}.xml')
                            for lane in ('parallel', 'serial', 'network')}
    receipt.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    return data


def main() -> int:
    subject = github_subject(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()), os.environ)
    receipts = list(Path(os.environ['RUNNER_TEMP']).glob('aiops-validation-*/receipt.json'))
    if len(receipts) != 1:
        raise ValueError('expected exactly one existing validation receipt; no evidence fabricated')
    sudo = shutil.which('sudo', path=os.defpath)
    data = enrich(receipts[0], subject, sudo)
    print(json.dumps({'github_subject': subject, 'receipt_status': data['status'],
                      'environment_capabilities': data['environment_capabilities']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
