"""P2b (disposable experiment): Git facts behind S1-B P3 §4 parity and D-B-GIT-FLOOR.

Uses the REAL S1-A producer (read-only import of master code) to publish a snapshot,
then runs `git cat-file --batch` descriptor-relative (fchdir(snapshot fd) + GIT_DIR=.,
cwd=None, NNP set, env allowlist, lazy fetch not enabled) and checks:
  F1  header grammar of every object line: <oid> SP <kind> SP (0|[1-9][0-9]{0,18}) LF
  F2  our canonical preimage digest == requested oid == git's own oid (sha1, sha256)
  F3  a full-length absent oid yields exactly "<oid> missing" (answered locally). Descendant
      processes are NOT observed here; lazy_fetch_structurally_irrelevant rests on the producer
      config + authored Git argv/env + the modelled acquisition path, not on this experiment
  F4  mutants of the preimage (omit type / SP / NUL, header size, body only, wrong kind)
      each fail the equality (the oracle discriminates)
Usage: git_facts.py <worktree> <out.json>
"""
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WT = sys.argv[1]
sys.path.insert(0, WT)
from app.agent_review.physical_snapshot_v2 import (  # noqa: E402
    CompletePublicationV2, DeclaredGitObjectFormatV2, PhysicalWorkBudgetV2, SnapshotPublicationRootV2,
    SourceRepositoryLocatorV2, publish_physical_snapshot_v2,
)
from app.agent_review.trusted_object_authority_v2 import AuthorizedGitStorageSetV2  # noqa: E402

HEADER = re.compile(rb"^([0-9a-f]{40}|[0-9a-f]{64}) (commit|tree|blob|tag) (0|[1-9][0-9]{0,18})\n$")
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_DIR": ".", "GIT_NO_REPLACE_OBJECTS": "1",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": "/dev/null",
       "GIT_TERMINAL_PROMPT": "0"}
GIT_ARGS = ["--no-replace-objects", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never", "-c", "safe.directory=*"]


def git_src(cwd, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd.parent), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x", "GIT_COMMITTER_NAME": "x",
           "GIT_COMMITTER_EMAIL": "x@x"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True).stdout


def make_repo(path: Path, fmt: str) -> None:
    path.mkdir(parents=True)
    git_src(path, "init", "-q", f"--object-format={fmt}", "-b", "main")
    for i in range(3):
        (path / f"f{i}.txt").write_text(f"content {i}\n" * (i + 1))
        git_src(path, "add", ".")
        git_src(path, "commit", "-q", "-m", f"c{i}")
    git_src(path, "repack", "-q", "-d")
    (path / "loose.txt").write_bytes(b"loose\x00object with NUL\n")
    git_src(path, "add", ".")
    git_src(path, "commit", "-q", "-m", "loose")
    git_src(path, "tag", "-a", "v1", "-m", "tag")


def digest(algo, kind: bytes, body: bytes) -> str:
    return hashlib.new(algo, kind + b" " + str(len(body)).encode() + b"\0" + body).hexdigest()


MUTANTS = {
    "omit_type": lambda a, k, b, hs: hashlib.new(a, b" " + str(len(b)).encode() + b"\0" + b).hexdigest(),
    "omit_sp": lambda a, k, b, hs: hashlib.new(a, k + str(len(b)).encode() + b"\0" + b).hexdigest(),
    "omit_nul": lambda a, k, b, hs: hashlib.new(a, k + b" " + str(len(b)).encode() + b).hexdigest(),
    "header_size_not_actual": lambda a, k, b, hs: hashlib.new(a, k + b" " + str(hs + 1).encode() + b"\0" + b).hexdigest(),
    "body_only": lambda a, k, b, hs: hashlib.new(a, b).hexdigest(),
    "wrong_kind": lambda a, k, b, hs: hashlib.new(a, (b"blob" if k != b"blob" else b"tree") + b" " + str(len(b)).encode() + b"\0" + b).hexdigest(),
}


def run_format(fmt: str, base: Path) -> dict:
    src = base / f"src-{fmt}" / "repo"
    make_repo(src, fmt)
    all_oids = git_src(src, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)").decode().split()
    expected = dict(zip(all_oids[0::2], all_oids[1::2]))
    pub = base / f"pub-{fmt}"
    (pub / "staging").mkdir(parents=True)
    (pub / "committed").mkdir()
    rfd = os.open(str(pub), os.O_RDONLY | os.O_DIRECTORY)
    root = SnapshotPublicationRootV2.from_directory_fd(rfd)
    os.close(rfd)
    cap = AuthorizedGitStorageSetV2.from_roots([str(src.parent)])
    budget = PhysicalWorkBudgetV2(max_descriptor_opens=100000, max_path_components=100000, max_entries_scanned=100000,
                                  max_pointers_followed=1000, max_pointer_bytes=10 ** 6, max_pointer_lines=10000,
                                  max_alternate_depth=16, max_source_bytes=10 ** 9, max_files_copied=100000)
    fmt_enum = DeclaredGitObjectFormatV2.SHA256 if fmt == "sha256" else DeclaredGitObjectFormatV2.SHA1
    out = publish_physical_snapshot_v2(source_authority=cap, source_locator=SourceRepositoryLocatorV2.absolute(str(src)),
                                       object_format=fmt_enum, physical_budget=budget, publication_root=root)
    assert type(out) is CompletePublicationV2, out
    snap = out.snapshot
    config = open(os.path.join(f"/proc/self/fd/{snap.committed_dir_fd}", "config")).read()
    algo = "sha256" if fmt == "sha256" else "sha1"
    absent = "0" * (64 if fmt == "sha256" else 40)
    req = "".join(o + "\n" for o in expected) + absent + "\n"
    libc = ctypes.CDLL(None, use_errno=True)

    def pre():
        os.fchdir(snap.committed_dir_fd)
        libc.prctl(38, 1, 0, 0, 0)

    p = subprocess.run(["/usr/bin/git", *GIT_ARGS, "cat-file", "--batch"], input=req.encode(), capture_output=True,
                       env=ENV, cwd=None, preexec_fn=pre, close_fds=True, timeout=30)
    data, pos, rows, bad_headers, parity_fail = p.stdout, 0, 0, [], []
    mutant_survivors = {m: 0 for m in MUTANTS}
    for oid in expected:
        nl = data.index(b"\n", pos)
        line = data[pos:nl + 1]
        m = HEADER.match(line)
        if not m or m.group(1).decode() != oid:
            bad_headers.append(line[:120].decode(errors="replace"))
            break
        kind, size = m.group(2), int(m.group(3))
        body = data[nl + 1:nl + 1 + size]
        assert data[nl + 1 + size:nl + 2 + size] == b"\n"
        pos = nl + 2 + size
        rows += 1
        if digest(algo, kind, body) != oid or kind.decode() != expected[oid]:
            parity_fail.append(oid)
        for name, fn in MUTANTS.items():
            if fn(algo, kind, body, size) == oid:
                mutant_survivors[name] += 1
    tail = data[pos:]
    snap.close()
    cap.close()
    root.close()
    return {"format": fmt, "objects": rows, "expected_objects": len(expected),
            "kinds": sorted(set(expected.values())), "bad_headers": bad_headers, "parity_failures": parity_fail,
            "missing_line_exact": tail == (absent + " missing\n").encode(), "rc": p.returncode,
            "stderr_bytes": len(p.stderr), "mutant_survivors": mutant_survivors,
            "producer_config": config, "config_has_remote_or_promisor": bool(re.search(r"remote|promisor|partialclone", config, re.I))}


def main() -> int:
    ver = subprocess.run(["/usr/bin/git", "version"], capture_output=True, text=True).stdout.strip()
    base = Path(tempfile.mkdtemp(prefix="s1b-p2b-"))
    res = {"git_version": ver, "formats": [run_format("sha1", base), run_format("sha256", base)]}
    ok = all(f["objects"] == f["expected_objects"] and not f["bad_headers"] and not f["parity_failures"]
             and f["missing_line_exact"] and f["rc"] == 0 and not any(f["mutant_survivors"].values())
             and not f["config_has_remote_or_promisor"] for f in res["formats"])
    res["verdict"] = "PASS" if ok else "FAIL"
    json.dump(res, open(sys.argv[2], "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "formats"}),
          [(f["format"], f["objects"], f["kinds"], f["missing_line_exact"], f["mutant_survivors"]) for f in res["formats"]])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
