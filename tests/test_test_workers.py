"""Capacity limits are simulated; host measurements never enter assertions."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from scripts import test_workers as w


def capacity(cpu=16, quota=None, cpuset=None, memory=32 * w.GIB):
    return dict(visible_cpu=cpu, quota_cpu=quota, cpuset_cpu=cpuset, memory_available_bytes=memory)


@pytest.fixture(autouse=True)
def independent_affinity(monkeypatch):
    # Simulated cgroups must not inherit the machine running these tests.
    monkeypatch.setattr(w.os, 'sched_getaffinity', lambda pid: set(range(32)), raising=False)


@pytest.mark.parametrize('observed,expected', [
    (capacity(), 4), (capacity(quota=2.8), 2), (capacity(cpuset=3), 3),
    (capacity(memory=4 * w.GIB), 2),
    (capacity(quota=6, cpuset=5, memory=3 * w.GIB), 1),
    (capacity(quota=0.5), 1), (capacity(memory=None), 1), (capacity(memory=0), 1),
])
def test_conservative_bounds(observed, expected):
    assert w.select_workers(observed)['selected_workers'] == expected


def test_explicit_override_and_auto_max():
    assert w.select_workers(capacity(cpu=1), '8')['selected_workers'] == 8
    assert w.select_workers(capacity(), auto_max=2)['selected_workers'] == 2


@pytest.mark.parametrize('invalid', ['', '0', '-1', 'auto', '1.5', ' 2', '+2', '２'])
def test_invalid_override_fails(invalid):
    with pytest.raises(ValueError, match='AIOPS_TEST_WORKERS'):
        w.select_workers(capacity(), invalid)


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_nested_v2_and_ancestor_limits(tmp_path):
    proc, cg = tmp_path / 'proc', tmp_path / 'cg'
    put(proc, 'self/cgroup', '0::/parent/child')
    put(proc, 'meminfo', 'MemAvailable: 16777216 kB\n')
    put(cg, 'cpu.max', 'max 100000')
    put(cg, 'parent/cpu.max', '250000 100000')
    put(cg, 'parent/child/cpu.max', '800000 100000')
    put(cg, 'parent/child/cpuset.cpus.effective', '0-3,8')
    put(cg, 'parent/memory.max', str(6 * w.GIB))
    put(cg, 'parent/memory.current', str(3 * w.GIB))
    observed = w.observe_capacity(proc, cg, visible_cpu=12)
    assert observed['quota_cpu'] == 2.5
    assert observed['cpuset_cpu'] == 5
    assert observed['memory_available_bytes'] == 3 * w.GIB
    assert w.select_workers(observed)['selected_workers'] == 1


def test_v1_fallback(tmp_path):
    proc, cg = tmp_path / 'proc', tmp_path / 'cg'
    put(proc, 'self/cgroup', '2:cpu,cpuacct:/group\n3:cpuset:/group\n4:memory:/group')
    put(proc, 'meminfo', 'MemAvailable: 33554432 kB')
    put(cg, 'cpu,cpuacct/group/cpu.cfs_quota_us', '350000')
    put(cg, 'cpu,cpuacct/group/cpu.cfs_period_us', '100000')
    put(cg, 'cpuset/group/cpuset.cpus', '0-1')
    put(cg, 'memory/group/memory.limit_in_bytes', str(8 * w.GIB))
    put(cg, 'memory/group/memory.usage_in_bytes', str(w.GIB))
    assert w.select_workers(w.observe_capacity(proc, cg, visible_cpu=16))['selected_workers'] == 2


def test_unrestricted_metadata_and_unknown_usage(tmp_path):
    proc, cg = tmp_path / 'proc', tmp_path / 'cg'
    put(proc, 'meminfo', 'MemAvailable: 33554432 kB')
    put(cg, 'memory.max', 'max')
    put(cg, 'cpu.max', 'max 100000')
    assert w.select_workers(w.observe_capacity(proc, cg, visible_cpu=32))['selected_workers'] == 4
    put(cg, 'memory.max', str(8 * w.GIB))
    assert w.select_workers(w.observe_capacity(proc, cg, visible_cpu=32))['selected_workers'] == 1


@pytest.mark.parametrize('value,expected', [('0-3,8,10-11', 7), ('0-2,1-3', 4), ('', None), ('3-1', None)])
def test_cpuset(value, expected):
    assert w.cpuset_count(value) == expected


def test_cli_safe_metadata_and_invalid_override(monkeypatch):
    monkeypatch.setenv('AIOPS_TEST_WORKERS', '2')
    monkeypatch.setenv('SECRET_TEST_SENTINEL', 'must-never-print-this')
    script = Path(w.__file__)
    for mode in ('--json', '--doctor'):
        result = subprocess.run([sys.executable, str(script), mode], text=True, capture_output=True)
        assert result.returncode == 0
        assert json.loads(result.stdout)['selected_workers'] == 2
        assert 'affinity_cpu' in json.loads(result.stdout)
        assert 'must-never-print-this' not in result.stdout
    monkeypatch.setenv('AIOPS_TEST_WORKERS', 'bad')
    result = subprocess.run([sys.executable, str(script), '--workers'], text=True, capture_output=True)
    assert result.returncode == 2 and 'positive integer' in result.stderr


@pytest.mark.parametrize('affinity,expected', [({2}, 1), ({1, 3}, 2)])
def test_process_affinity_discriminator(tmp_path, monkeypatch, affinity, expected):
    proc, cg = tmp_path / 'proc', tmp_path / 'cg'
    put(proc, 'meminfo', f'MemAvailable: {5 * w.GIB // 1024} kB')
    put(cg, 'cpuset.cpus.effective', '0-3')
    monkeypatch.setattr(w.os, 'cpu_count', lambda: 4)
    monkeypatch.setattr(w.os, 'sched_getaffinity', lambda pid: affinity)
    observed = w.observe_capacity(proc, cg)
    assert observed['visible_cpu'] == observed['cpuset_cpu'] == 4
    assert w.select_workers(observed)['memory_bound'] == 3
    assert w.select_workers(observed)['selected_workers'] == expected
    assert observed['affinity_cpu'] == expected
    explicit = w.select_workers(observed, '8')
    assert explicit['selected_workers'] == 8 and explicit['worker_source'] == 'explicit'


@pytest.mark.parametrize('unavailable', ['absent', 'oserror', 'unimplemented', 'empty', 'shape', 'member'])
def test_unknown_affinity_keeps_other_bounds(tmp_path, monkeypatch, unavailable):
    if unavailable == 'absent':
        monkeypatch.delattr(w.os, 'sched_getaffinity')
    else:
        def query(pid):
            if unavailable == 'oserror': raise OSError('unsupported')
            if unavailable == 'unimplemented': raise NotImplementedError
            return {'empty': set(), 'shape': [0], 'member': {'bad'}}[unavailable]
        monkeypatch.setattr(w.os, 'sched_getaffinity', query)
    proc, cg = tmp_path / 'proc', tmp_path / 'cg'
    put(proc, 'meminfo', f'MemAvailable: {5 * w.GIB // 1024} kB')
    put(cg, 'cpuset.cpus.effective', '0-3')
    put(cg, 'cpu.max', '200000 100000')
    observed = w.observe_capacity(proc, cg, visible_cpu=4)
    assert observed['affinity_cpu'] is None
    assert w.select_workers(observed)['selected_workers'] == 2
