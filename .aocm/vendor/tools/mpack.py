#!/usr/bin/env python3
"""AOCM-MPACK bootstrap: byte integrity, non-overwriting install, Git observation.

Not a semantic qualifier, upstream CAEM runtime, authority issuer, or CI runner.
TCB: trusted Python/Git/OS and a quiescent local filesystem. No hostile-host claim.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MANIFEST = 'PACK_MANIFEST.json'
HEX = re.compile(r'^[0-9a-f]{64}$')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{s}{i}' for s in ('COM', 'LPT') for i in range(1, 10)}

class PackError(ValueError):
    """Explicit refusal; never a positive qualification disposition."""

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _unique_object(pairs):
    obj = {}
    for key, val in pairs:
        if key in obj:
            raise PackError(f'DUPLICATE_JSON_KEY: {key}')
        obj[key] = val
    return obj

def _invalid_constant(value):
    raise PackError(f'NON_JSON_CONSTANT: {value}')

def read_json_bytes(data: bytes):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f'INVALID_JSON: {exc}') from exc

def valid_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or not re.fullmatch(r'[A-Za-z0-9._/-]+', value):
        raise PackError('UNSAFE_PATH: characters or empty path')
    parts = value.split('/')
    if any(p in ('', '.', '..') or p.endswith('.') or p.split('.')[0].upper() in RESERVED for p in parts):
        raise PackError(f'UNSAFE_PATH: {value}')
    if PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value:
        raise PackError(f'UNSAFE_PATH: {value}')
    return value

def _plain_file(path: Path) -> bytes:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise PackError(f'NON_REGULAR_FILE: {path.name}')
    if st.st_size > MAX_FILE_BYTES:
        raise PackError('FILE_SIZE_LIMIT')
    data = path.read_bytes()  # exactly these bytes are subsequently hashed and copied
    if len(data) > MAX_FILE_BYTES:
        raise PackError('FILE_SIZE_LIMIT')
    return data

def _census(root: Path) -> set[str]:
    result: set[str] = set()
    for here, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            p = Path(here) / name
            if not stat.S_ISDIR(p.lstat().st_mode):
                raise PackError(f'NON_REGULAR_DIRECTORY: {name}')
        for name in files:
            p = Path(here) / name
            if not stat.S_ISREG(p.lstat().st_mode):
                raise PackError(f'NON_REGULAR_FILE: {name}')
            result.add(valid_relative_path(p.relative_to(root).as_posix()))
            if len(result) > MAX_FILES + 1:
                raise PackError('FILE_COUNT_LIMIT')
    return result

@dataclass(frozen=True)
class LoadedPack:
    version: str
    manifest_bytes: bytes
    manifest_digest: str
    files: Mapping[str, bytes]

def load_pack(root: Path, expected_manifest: str | None = None) -> LoadedPack:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise PackError('INVALID_PACK_ROOT')
    if expected_manifest is not None and not HEX.fullmatch(expected_manifest):
        raise PackError('INVALID_EXPECTED_MANIFEST_DIGEST')
    raw = _plain_file(root / MANIFEST)
    digest = sha256(raw)
    if expected_manifest is not None and digest != expected_manifest:
        raise PackError('MANIFEST_ANCHOR_MISMATCH')
    m = read_json_bytes(raw)
    required = {'schema_version', 'pack_id', 'version', 'hash_algorithm', 'files'}
    if type(m) is not dict or set(m) != required:
        raise PackError('MANIFEST_SHAPE')
    if type(m['schema_version']) is not int or m['schema_version'] != 1 or m['pack_id'] != 'AOCM-MPACK' or m['hash_algorithm'] != 'sha256':
        raise PackError('MANIFEST_CONTRACT')
    if not isinstance(m['version'], str) or not re.fullmatch(r'[0-9A-Za-z.-]+', m['version']):
        raise PackError('MANIFEST_VERSION')
    rows = m['files']
    if type(rows) is not list or not rows or len(rows) > MAX_FILES:
        raise PackError('MANIFEST_FILE_DOMAIN')
    by_path = {}
    folded = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'path', 'size', 'sha256'}:
            raise PackError('MANIFEST_ROW_SHAPE')
        p = valid_relative_path(row['path'])
        if p == MANIFEST or p in by_path or p.casefold() in folded:
            raise PackError('DUPLICATE_OR_SELF_MANIFEST_MEMBER')
        if type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE_BYTES:
            raise PackError('MANIFEST_SIZE')
        if type(row['sha256']) is not str or not HEX.fullmatch(row['sha256']):
            raise PackError('MANIFEST_MEMBER_DIGEST')
        by_path[p] = row
        folded.add(p.casefold())
    if list(by_path) != sorted(by_path):
        raise PackError('NON_CANONICAL_MANIFEST_ORDER')
    actual = _census(root)
    if actual != set(by_path) | {MANIFEST}:
        raise PackError('MEMBERSHIP_MISMATCH: missing=' + repr(sorted(set(by_path) - actual)) +
                        ' extra=' + repr(sorted(actual - set(by_path) - {MANIFEST})))
    captured = {}
    total = 0
    for p, row in by_path.items():
        data = _plain_file(root / p)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PackError('TOTAL_SIZE_LIMIT')
        if len(data) != row['size'] or sha256(data) != row['sha256']:
            raise PackError(f'MEMBER_BYTES_MISMATCH: {p}')
        captured[p] = data
    # Mandatory local carriers prevent an empty or arbitrary manifest from pretending to be this pack.
    needed = {'PACK.json', 'method/CORE.md', 'method/PR_LIFECYCLE.md',
              'provenance/SOURCE_REGISTRY.json', 'provenance/KNOWLEDGE_REGISTER.json',
              'templates/repository-profile.json', 'tools/mpack.py'}
    if not needed <= captured.keys():
        raise PackError('MISSING_REQUIRED_PACK_CARRIER')
    meta = read_json_bytes(captured['PACK.json'])
    if not isinstance(meta, dict) or meta.get('pack_id') != m['pack_id'] or meta.get('version') != m['version']:
        raise PackError('PACK_MANIFEST_IDENTITY_MISMATCH')
    return LoadedPack(m['version'], raw, digest, MappingProxyType(captured))

def git_root(repo: Path) -> tuple[Path, str]:
    raw = Path(repo).absolute()
    if raw.is_symlink() or not raw.is_dir():
        raise PackError('REPOSITORY_ROOT_REQUIRED')
    try:
        run = subprocess.run(['git', '-C', str(raw), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, check=True, timeout=15)
        root = Path(run.stdout.strip()).resolve()
        if raw.resolve() != root:
            raise PackError('EXACT_REPOSITORY_ROOT_REQUIRED')
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                              capture_output=True, text=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise PackError('GIT_REPOSITORY_UNAVAILABLE') from exc
    return root, head.stdout.strip() if head.returncode == 0 else 'UNBORN'

def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')

def _exclusive_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as fh:
        fh.write(data)

def _validate_install_snapshot(pack: LoadedPack):
    # LoadedPack is a Python data carrier, not an unforgeable capability token.
    # Programmatic callers get the same path/byte safety boundary as the CLI.
    if not isinstance(pack, LoadedPack) or sha256(pack.manifest_bytes) != pack.manifest_digest:
        raise PackError('INSTALL_SNAPSHOT_BINDING')
    m = read_json_bytes(pack.manifest_bytes)
    if type(m) is not dict or m.get('version') != pack.version or type(m.get('files')) is not list:
        raise PackError('INSTALL_SNAPSHOT_MANIFEST')
    rows = m['files']
    if not 0 < len(rows) <= MAX_FILES:
        raise PackError('INSTALL_SNAPSHOT_DOMAIN')
    expected = {}
    for row in rows:
        if type(row) is not dict or set(row) != {'path','size','sha256'}:
            raise PackError('INSTALL_SNAPSHOT_ROW')
        name = valid_relative_path(row['path'])
        if name == MANIFEST or name in expected:
            raise PackError('INSTALL_SNAPSHOT_MEMBER')
        expected[name] = row
    if set(pack.files) != set(expected):
        raise PackError('INSTALL_SNAPSHOT_MEMBERSHIP')
    total = 0
    for name, data in pack.files.items():
        valid_relative_path(name)
        if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
            raise PackError('INSTALL_SNAPSHOT_BYTES')
        total += len(data)
        if total > MAX_TOTAL_BYTES or len(data) != expected[name]['size'] or sha256(data) != expected[name]['sha256']:
            raise PackError('INSTALL_SNAPSHOT_BYTES')


def install(pack: LoadedPack, repo: Path, apply: bool = False) -> dict:
    _validate_install_snapshot(pack)
    root, head = git_root(repo)
    target = root / '.aocm'
    if os.path.lexists(target):
        raise PackError('DESTINATION_EXISTS_NO_OVERWRITE')
    report = dict(operation='INSTALL_PLAN',apply=False, destination=str(target),version=pack.version,
                  manifest_sha256=pack.manifest_digest, files=len(pack.files),
                  adoption='UNADOPTED', repository_head_observed=head, semantic_verdict_issued=False)
    if not apply:
        return report
    # Reserve exclusively. No recursive deletion on failure: never delete another actor's content.
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise PackError('DESTINATION_EXISTS_NO_OVERWRITE') from exc
    marker = target/'INSTALL_INCOMPLETE.json'
    _exclusive_write(marker, _json_bytes({'state':'INSTALL_IN_PROGRESS','manifest_sha256':pack.manifest_digest}))
    try:
        vendor=target/'vendor'
        vendor.mkdir()
        for name, data in pack.files.items():
            _exclusive_write(vendor/name,data)
        _exclusive_write(vendor/MANIFEST, pack.manifest_bytes)
        load_pack(vendor, pack.manifest_digest)
        profile=read_json_bytes(pack.files['templates/repository-profile.json'])
        profile['repository']=root.name
        profile['observed_head']=head
        profile['observed_at']=datetime.now(timezone.utc).isoformat()
        _exclusive_write(target/'repository-profile.json',_json_bytes(profile))
        _exclusive_write(target/'ENTRYPOINT.md',b'# Installed MPACK entry\n\nRead `vendor/agents/ENTRYPOINT.md`, `vendor/method/CORE.md` and `repository-profile.json`. Resolve vendor document paths relative to vendor/. Profile remains UNADOPTED until an actual maintainer decision.\n')
        _exclusive_write(target/'ADOPTION.md',b'# Local adoption\n\nThis directory was created without modifying root agent instructions, Git settings or CI. Inspect vendor/INSTALL.md, propose repository-profile.json from real sources, and record a competent maintainer adoption decision. Existing instructions must be reconciled manually, not overwritten. Installation is not qualification or authorization.\n')
        report.update(operation='INSTALL_COMPLETED',apply=True)
        _exclusive_write(target/'INSTALL_RECEIPT.json',_json_bytes(report))
        marker.unlink()
    except Exception as exc:
        raise PackError('INSTALL_INCOMPLETE: marker retained; inspect locally before any retry') from exc
    return report

def doctor(repo: Path) -> dict:
    root, head=git_root(repo)
    target=root/'.aocm'
    result=dict(operation='LOCAL_OBSERVATION',repository=str(root),head_observed=head,
                observation_scope='local Git and pack bytes only',semantic_verdict_issued=False)
    if not os.path.lexists(target):
        result['installation']='ABSENT'
        return result
    if target.is_symlink() or not target.is_dir():
        raise PackError('INSTALLATION_PATH_INVALID')
    if (target/'INSTALL_INCOMPLETE.json').exists():
        raise PackError('INSTALLATION_INCOMPLETE')
    installed=load_pack(target/'vendor')
    profile=read_json_bytes(_plain_file(target/'repository-profile.json'))
    if not isinstance(profile,dict):
        raise PackError('PROFILE_SHAPE')
    result.update(installation='BYTE_INTEGRITY_VALID',version=installed.version,
                  profile_lifecycle_declared=profile.get('lifecycle','UNKNOWN'),
                  authority_authentication='NOT_PERFORMED')
    return result

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    v=subs.add_parser('verify',help='Verify manifest membership and exact bytes; not semantics')
    v.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    v.add_argument('--expect-manifest')
    i=subs.add_parser('install',help='Dry-run by default; creates only a new .aocm directory')
    i.add_argument('--repo',type=Path,required=True)
    i.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    i.add_argument('--expect-manifest')
    i.add_argument('--apply',action='store_true')
    d=subs.add_parser('doctor',help='Read local Git and installed package state')
    d.add_argument('--repo',type=Path,required=True)
    a=parser.parse_args(argv)
    try:
        if a.command=='verify':
            pack=load_pack(a.root,a.expect_manifest)
            output=dict(disposition='PACKAGE_INTEGRITY_VALID',version=pack.version,files=len(pack.files),
                        manifest_sha256=pack.manifest_digest,external_anchor_checked=a.expect_manifest is not None,
                        semantic_verdict_issued=False,authority_granted=False)
        elif a.command=='install':
            output=install(load_pack(a.root,a.expect_manifest),a.repo,a.apply)
        else:
            output=doctor(a.repo)
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return 0
    except (PackError,OSError) as exc:
        print(json.dumps({'disposition':'REFUSED','reason':str(exc),'semantic_verdict_issued':False},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
