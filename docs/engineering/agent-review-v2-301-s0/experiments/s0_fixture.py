"""EXPERIMENTAL ONLY -- Git fixtures with their own declared identity (no global config read)."""
from __future__ import annotations

import os
import subprocess
import zlib
from pathlib import Path

FIXTURE_ENV = {
    "PATH": os.defpath,
    "HOME": "/nonexistent",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "s0-fixture",
    "GIT_AUTHOR_EMAIL": "s0-fixture@invalid",
    "GIT_COMMITTER_NAME": "s0-fixture",
    "GIT_COMMITTER_EMAIL": "s0-fixture@invalid",
    "GIT_AUTHOR_DATE": "2026-09-26T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-09-26T00:00:00Z",
}


def git(repo: Path, *args: str, data: bytes | None = None) -> str:
    cp = subprocess.run(["git", *args], cwd=repo, input=data, env=FIXTURE_ENV, capture_output=True, timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {cp.stderr.decode(errors='replace')[:300]}")
    return cp.stdout.decode("ascii", "replace").strip()


def init(repo: Path, object_format: str = "sha1") -> Path:
    repo.mkdir(parents=True)
    git(repo, "init", "-q", f"--object-format={object_format}")
    return repo


def blob(repo: Path, data: bytes) -> str:
    return git(repo, "hash-object", "-w", "--stdin", data=data)


def mktree(repo: Path, entries: list[tuple[str, str, str, bytes]]) -> str:
    """entries: (mode, type, oid, raw name). `git mktree -z` validates and sorts."""
    payload = b"".join(f"{m} {t} {o}\t".encode() + name + b"\0" for m, t, o, name in entries)
    return git(repo, "mktree", "-z", data=payload)


def raw_tree(repo: Path, entries: list[tuple[str, str, bytes]]) -> str:
    """Write a tree object from raw (mode, oid, name) WITHOUT git's validation (negative corpus only)."""
    body = b"".join(m.encode() + b" " + name + b"\0" + bytes.fromhex(o) for m, o, name in entries)
    return git(repo, "hash-object", "-t", "tree", "-w", "--literally", "--stdin", data=body)


def commit(repo: Path, tree: str, message: str = "s0") -> str:
    return git(repo, "commit-tree", tree, "-m", message)


def empty_tree(repo: Path) -> str:
    return git(repo, "hash-object", "-t", "tree", "-w", "--stdin", data=b"")


def loose_path(repo: Path, oid: str) -> Path:
    return repo / ".git" / "objects" / oid[:2] / oid[2:]


def swap_loose(repo: Path, oid: str, kind: str, body: bytes) -> None:
    """Same-UID storage swap: a VALID zlib object with different bytes under the old name."""
    p = loose_path(repo, oid)
    os.chmod(p, 0o644)
    p.write_bytes(zlib.compress(kind.encode() + b" %d\0" % len(body) + body))
