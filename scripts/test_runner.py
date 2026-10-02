#!/usr/bin/env python3
"""Canonical offline pytest runner. Lanes sequentially share one budget."""
from __future__ import annotations
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import shlex
from scripts.test_lanes import LANES, ORDINARY
from scripts.test_workers import characterize


def pytest_command(lane: str, *, serial: bool, workers: int, xdist: bool, extra: list[str]) -> list[str]:
    expression = ORDINARY if lane == 'ordinary' else LANES[lane]
    command = [sys.executable, '-m', 'pytest', '-q', '--tb=short', '-m', expression]
    collecting = any(arg in ('--co', '--collect-only') for arg in extra)
    if lane == 'parallel' and not serial and not collecting and xdist:
        command += ['-n', str(workers), '--dist=loadfile']
    return command + extra


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', action='store_true')
    parser.add_argument('--lane', choices=('ordinary', *LANES), default='ordinary')
    args, extra = parser.parse_known_args()
    if any(arg.startswith(('-m', '-n', '--numprocesses', '--dist'))
           for arg in extra + shlex.split(os.environ.get('PYTEST_ADDOPTS', ''))):
        parser.error('marker/worker overrides belong to runner; use --lane / --serial / AIOPS_TEST_WORKERS')
    os.chdir(Path(__file__).resolve().parent.parent)
    try:
        capacity = characterize()
    except ValueError as exc:
        parser.error(str(exc))
    xdist = importlib.util.find_spec('xdist') is not None
    if not xdist and not args.serial:
        print('[test] pytest-xdist unavailable; explicit serial fallback', flush=True)
    collecting = any(arg in ('--co', '--collect-only') for arg in extra)
    if os.environ.get('AIOPS_INTEGRATION') == '1':
        if args.lane != 'ordinary':
            parser.error('AIOPS_INTEGRATION=1 cannot be combined with an offline lane')
        print('[test] legacy integration opt-in: serial execution; external services required', flush=True)
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', '--tb=short', *extra]).returncode
    lanes = ['parallel', 'serial'] if args.lane == 'ordinary' and not args.serial and not collecting else [args.lane]
    if len(lanes) > 1 and any(arg.startswith(('--junitxml', '--junit-xml')) for arg in extra):
        parser.error('one JUnit report needs --serial or an explicit --lane; use local_validate.sh for full lane reports')
    results = []
    for lane in lanes:
        command = pytest_command(lane, serial=args.serial, workers=capacity['selected_workers'], xdist=xdist, extra=extra)
        print(f'[test] lane={lane} budget={capacity["selected_workers"]} command={command}', flush=True)
        result = subprocess.run(command).returncode
        results.append(result)
        if result not in (0, 5):
            return result
        if result == 5:
            print(f'[test] lane={lane}: SkippedByScope (no selected tests)', flush=True)
    return 0 if 0 in results else 5


if __name__ == '__main__':
    raise SystemExit(main())
