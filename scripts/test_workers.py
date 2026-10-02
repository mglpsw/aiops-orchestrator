#!/usr/bin/env python3
"""Conservative test capacity selection; stdlib only, no application imports."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path

GIB = 1024 ** 3
RESERVED_MEMORY = 2 * GIB
MEMORY_PER_WORKER = GIB
AUTO_MAX = 4


def positive(value: str, name: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError(f'{name} must be a positive integer')
    return int(value)


def cpuset_count(value: str) -> int | None:
    cpus = set()
    try:
        for part in value.split(','):
            ends = part.split('-')
            first, last = int(ends[0]), int(ends[-1])
            if len(ends) > 2 or first < 0 or last < first or last > 1_000_000:
                return None
            cpus.update(range(first, last + 1))
    except ValueError:
        return None
    return len(cpus) or None


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return ''


def limit(value: str) -> int | None:
    try:
        number = int(value)
        return number if 0 <= number < 2 ** 60 else None
    except ValueError:
        return None


def affinity_count() -> int | None:
    """Process affinity can be narrower than the host/cgroup CPU bounds."""
    query = getattr(os, 'sched_getaffinity', None)
    if query is None:
        return None
    try:
        cpus = query(0)
        if not isinstance(cpus, (set, frozenset)) or not cpus or any(
                type(cpu) is not int or cpu < 0 for cpu in cpus):
            return None
        return len(cpus)
    except (OSError, NotImplementedError):
        return None


def observe_capacity(proc: Path = Path('/proc'), cgroup: Path = Path('/sys/fs/cgroup'),
                     visible_cpu: int | None = None) -> dict:
    """Observe membership and ancestor limits, including namespace-root limits.

    Conventional v1 controller mounts are supported. Unknown memory falls back
    to one worker; an unknown usage on a finite limit establishes no spare RAM.
    """
    directories = {cgroup}
    for line in read(proc / 'self/cgroup').splitlines():
        fields = line.split(':', 2)
        if len(fields) != 3:
            continue
        _, controllers, member = fields
        roots = [cgroup] if not controllers else [cgroup / c for c in controllers.split(',')]
        if 'cpu' in controllers.split(','):
            roots.append(cgroup / 'cpu,cpuacct')
        for root in roots:
            current = root / member.lstrip('/')
            if '..' in current.parts:
                continue
            while current != root:
                directories.add(current)
                current = current.parent
            directories.add(root)
    directories.update(cgroup / n for n in ('cpu', 'cpu,cpuacct', 'cpuset', 'memory'))
    quotas, sets, spare = [], [], []
    for directory in sorted(directories):
        parts = read(directory / 'cpu.max').split()
        try:
            if len(parts) == 2 and parts[0] != 'max' and int(parts[0]) > 0 and int(parts[1]) > 0:
                quotas.append(int(parts[0]) / int(parts[1]))
            else:
                quota = int(read(directory / 'cpu.cfs_quota_us'))
                period = int(read(directory / 'cpu.cfs_period_us'))
                if quota > 0 and period > 0:
                    quotas.append(quota / period)
        except ValueError:
            pass
        count = cpuset_count(read(directory / 'cpuset.cpus.effective') or
                             read(directory / 'cpuset.effective_cpus') or read(directory / 'cpuset.cpus'))
        if count is not None:
            sets.append(count)
        maximum = limit(read(directory / 'memory.max') or read(directory / 'memory.limit_in_bytes'))
        usage = limit(read(directory / 'memory.current') or read(directory / 'memory.usage_in_bytes'))
        if maximum is not None:
            spare.append(max(0, maximum - usage) if usage is not None else 0)
    host_available = None
    for line in read(proc / 'meminfo').splitlines():
        if line.startswith('MemAvailable:'):
            try:
                host_available = int(line.split()[1]) * 1024
            except (ValueError, IndexError):
                pass
    remaining = spare + ([host_available] if host_available is not None else [])
    return {'visible_cpu': max(1, visible_cpu or os.cpu_count() or 1),
            'affinity_cpu': affinity_count(),
            'quota_cpu': min(quotas) if quotas else None,
            'cpuset_cpu': min(sets) if sets else None,
            'host_memory_available_bytes': host_available,
            'cgroup_memory_remaining_bytes': min(spare) if spare else None,
            'memory_available_bytes': min(remaining) if remaining else None}


def select_workers(capacity: dict, override: str | None = None, auto_max: int = AUTO_MAX) -> dict:
    if auto_max < 1:
        raise ValueError('AIOPS_TEST_AUTO_MAX must be a positive integer')
    cpu = [capacity['visible_cpu']]
    if capacity['quota_cpu'] is not None:
        cpu.append(max(1, math.floor(capacity['quota_cpu'])))
    if capacity['cpuset_cpu'] is not None:
        cpu.append(capacity['cpuset_cpu'])
    if capacity.get('affinity_cpu') is not None:
        cpu.append(max(1, capacity['affinity_cpu']))
    effective = max(1, min(cpu))
    memory = capacity['memory_available_bytes']
    memory_bound = max(1, (memory - RESERVED_MEMORY) // MEMORY_PER_WORKER) if memory is not None else 1
    workers = positive(override, 'AIOPS_TEST_WORKERS') if override is not None else min(effective, memory_bound, auto_max)
    return {**capacity, 'effective_cpu': effective, 'memory_bound': memory_bound,
            'selected_workers': workers, 'worker_source': 'explicit' if override is not None else 'capacity',
            'auto_max': auto_max, 'reserved_memory_bytes': RESERVED_MEMORY,
            'memory_per_worker_bytes': MEMORY_PER_WORKER}


def characterize() -> dict:
    return select_workers(observe_capacity(), os.environ.get('AIOPS_TEST_WORKERS'),
                          positive(os.environ.get('AIOPS_TEST_AUTO_MAX', str(AUTO_MAX)), 'AIOPS_TEST_AUTO_MAX'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--workers', action='store_true')
    mode.add_argument('--json', action='store_true')
    mode.add_argument('--doctor', action='store_true')
    args = parser.parse_args()
    try:
        data = characterize()
    except ValueError as exc:
        parser.error(str(exc))
    print(data['selected_workers'] if args.workers else json.dumps(data, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
