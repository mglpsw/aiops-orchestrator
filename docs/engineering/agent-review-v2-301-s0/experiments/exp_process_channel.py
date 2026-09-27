"""EXP-PROC: process integrity and result channel vs a same-UID process (not the object).

usage: python -I -S exp_process_channel.py <interpreter>
Separates: object integrity (sealed memfd, exp_capture_stability), PROCESS integrity (can a
same-UID process write the consumer's memory or reach its descriptors?) and RESULT-CHANNEL
integrity (can it inject a forged result?). Observations are made by this harness, outside the
consumer, never by the consumer's self-report.
"""
import json
import os
import socket
import subprocess
import sys
import time

PY = sys.argv[1]
out = {"cases": {}, "environment": {}}
try:
    out["environment"]["yama_ptrace_scope"] = open("/proc/sys/kernel/yama/ptrace_scope").read().strip()
except OSError as exc:
    out["environment"]["yama_ptrace_scope"] = f"unavailable:{exc.errno}"


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


CHILD_PIPE = r"""
import os, sys, time
time.sleep(1.5)
sys.stdout.write('{"result":"genuine"}\n'); sys.stdout.flush()
"""
CHILD_SOCK = r"""
import os, sys, time
fd = int(sys.argv[1]); time.sleep(1.5); os.write(fd, b'{"result":"genuine"}\n')
"""
CHILD_WAIT = r"""
import ctypes, os, sys, time
if sys.argv[1] == "nodump":
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
print("ready", flush=True); time.sleep(5)
"""
INJECT = r"""
import os, sys
try:
    fd = os.open(sys.argv[1], os.O_WRONLY); os.write(fd, b'{"result":"FORGED"}\n'); print("INJECTED")
except OSError as e:
    print("denied:%d" % e.errno)
"""
MEMWRITE = r"""
import ctypes, os, sys
pid = int(sys.argv[1]); r = {}
try:
    fd = os.open("/proc/%d/mem" % pid, os.O_RDWR); r["proc_mem_open_rdwr"] = "SUCCEEDED"
except OSError as e:
    r["proc_mem_open_rdwr"] = "denied:%d" % e.errno
libc = ctypes.CDLL(None, use_errno=True)
PTRACE_ATTACH = 16
rc = libc.ptrace(PTRACE_ATTACH, pid, None, None)
r["ptrace_attach"] = "SUCCEEDED" if rc == 0 else "denied:%d" % ctypes.get_errno()
if rc == 0:
    libc.ptrace(17, pid, None, None)  # PTRACE_DETACH
try:
    os.listdir("/proc/%d/fd" % pid); r["proc_fd_list"] = "SUCCEEDED"
except OSError as e:
    r["proc_fd_list"] = "denied:%d" % e.errno
print(__import__("json").dumps(r))
"""


def attacker(src, arg):
    return subprocess.run([PY, "-I", "-S", "-c", src, arg], capture_output=True, text=True, timeout=30).stdout.strip()


# result channel = pipe: a same-UID process reopens the child's write end through /proc
child = subprocess.Popen([PY, "-I", "-S", "-c", CHILD_PIPE], stdout=subprocess.PIPE, text=True)
time.sleep(0.3)
inj = attacker(INJECT, f"/proc/{child.pid}/fd/1")
first = child.stdout.readline().strip()
child.wait()
case("pipe_result_channel_injectable", {"inject": "INJECTED", "first_line_read": '{"result":"FORGED"}'},
     {"inject": inj, "first_line_read": first})

# result channel = socketpair: /proc/<pid>/fd/<n> of a socket cannot be reopened
parent_end, child_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
n = child_end.fileno()
child = subprocess.Popen([PY, "-I", "-S", "-c", CHILD_SOCK, str(n)], pass_fds=(n,))
child_end.close()
time.sleep(0.3)
inj = attacker(INJECT, f"/proc/{child.pid}/fd/{n}")
child.wait()
got = parent_end.recv(1024).decode().strip()
parent_end.close()
case("socketpair_result_channel_not_reopenable", {"inject": "denied:6", "first_line_read": '{"result":"genuine"}'},
     {"inject": inj, "first_line_read": got}, note="errno 6 = ENXIO")

# process memory / ptrace / descriptor listing, with and without PR_SET_DUMPABLE(0)
for mode in ("dumpable", "nodump"):
    child = subprocess.Popen([PY, "-I", "-S", "-c", CHILD_WAIT, mode], stdout=subprocess.PIPE, text=True)
    child.stdout.readline()
    res = json.loads(attacker(MEMWRITE, str(child.pid)))
    child.kill()
    child.wait()
    out["cases"][f"same_uid_non_ancestor_vs_{mode}_child"] = {"observed": res}

out["cases"]["same_uid_non_ancestor_vs_dumpable_child"]["interpretation"] = (
    "with Yama ptrace_scope=1 a non-ancestor cannot attach or open /proc/pid/mem for write; it CAN list "
    "the child's descriptors while the child is dumpable (exec resets dumpable to 1)")
print(json.dumps(out, indent=1))
