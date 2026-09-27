"""EXP-N1: authentication AT CONSUMPTION of every commit/tree/blob contribution to S.

usage: python -I -S exp_n1_auth.py <toolrepo_source_root> <scratch_dir>
Each case records expected vs observed; `pass` is computed, never asserted by prose.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SRC = Path(sys.argv[1]).resolve()
sys.path.insert(1, str(SRC))
W = Path(sys.argv[2]).resolve()

import s0_capture as cap  # noqa: E402
import s0_fixture as fx  # noqa: E402

out = {"cases": {}}


def jsonable(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "backslashreplace")
    if isinstance(v, dict):
        return {jsonable(k): jsonable(x) for k, x in v.items()}
    return v


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": jsonable(expected), "observed": jsonable(observed),
                          "pass": expected == observed, **jsonable(extra)}


def outcome(fn):
    try:
        fn()
        return "ACCEPTED"
    except cap.CaptureRefused as exc:
        kind = "/" + exc.detail.split()[0] if exc.reason == "object_hash_mismatch" else ""
        return "REFUSED:" + exc.reason + kind  # names WHICH contribution failed, not just "a" failure


def fixture(root: Path, fmt: str = "sha1"):
    repo = fx.init(root, fmt)
    b_init = fx.blob(repo, b'VALUE = "trusted"\n')
    b_mod = fx.blob(repo, b'VALUE = "mod-trusted"\n')
    pkg = fx.mktree(repo, [("100644", "blob", b_init, b"__init__.py"), ("100644", "blob", b_mod, b"mod.py")])
    root_tree = fx.mktree(repo, [("040000", "tree", pkg, b"pkg")])
    c = fx.commit(repo, root_tree)
    return repo, c, root_tree, pkg, b_init


# --- control + each contribution tampered (sha1) -------------------------------------------
for fmt in ("sha1", "sha256"):
    repo, c, root_tree, pkg, b_init = fixture(W / f"n1-{fmt}", fmt)
    s = cap.build_subject(repo, c)
    case(f"{fmt}_control_legitimate_bytes_accepted",
         {b"pkg": "tree", b"pkg/__init__.py": b'VALUE = "trusted"\n'},
         {p: (k if k == "tree" else v) for p, (k, _o, v) in s.nodes.items() if p in (b"pkg", b"pkg/__init__.py")},
         algorithm=s.algorithm)

    evil_same_len = b'VALUE = "EVIL!!!"\n'
    assert len(evil_same_len) == len(b'VALUE = "trusted"\n')
    fx.swap_loose(repo, b_init, "blob", evil_same_len)
    git_returns = subprocess.run(["git", "cat-file", "-p", b_init], cwd=repo, env=fx.FIXTURE_ENV,
                                 capture_output=True).stdout
    case(f"{fmt}_HOR_git_itself_serves_swapped_blob_rc0", True, b"EVIL" in git_returns)
    case(f"{fmt}_blob_swapped_same_length", "REFUSED:object_hash_mismatch/blob", outcome(lambda: cap.build_subject(repo, c)))
    fx.swap_loose(repo, b_init, "blob", b"x = 'a different length payload'\n")
    case(f"{fmt}_blob_swapped_other_length", "REFUSED:object_hash_mismatch/blob", outcome(lambda: cap.build_subject(repo, c)))
    # ablation: the discriminator is the hash check, not an incidental failure
    mutant = {}

    def run_mutant():
        mutant["s"] = cap.build_subject(repo, c, verify=False)

    case(f"{fmt}_ABLATION_verify_disabled_accepts_swapped_blob", "ACCEPTED", outcome(run_mutant),
         mutant_embedded=mutant["s"].nodes[b"pkg/__init__.py"][2].decode() if mutant else None)
    fx.swap_loose(repo, b_init, "blob", b'VALUE = "trusted"\n')  # restore exact bytes
    case(f"{fmt}_restored_blob_accepted_again", "ACCEPTED", outcome(lambda: cap.build_subject(repo, c)))

    # tree contribution: replace pkg tree object with a valid tree naming another blob
    evil_blob = fx.blob(repo, b'VALUE = "EVIL-TREE"\n')
    raw = subprocess.run(["git", "cat-file", "tree", fx.mktree(repo, [("100644", "blob", evil_blob, b"__init__.py")])],
                         cwd=repo, env=fx.FIXTURE_ENV, capture_output=True).stdout
    original_pkg = subprocess.run(["git", "cat-file", "tree", pkg], cwd=repo, env=fx.FIXTURE_ENV, capture_output=True).stdout
    fx.swap_loose(repo, pkg, "tree", raw)
    case(f"{fmt}_tree_swapped", "REFUSED:object_hash_mismatch/tree", outcome(lambda: cap.build_subject(repo, c)))
    fx.swap_loose(repo, pkg, "tree", original_pkg)

    # commit contribution: replace the commit object with one naming another root tree
    other_root = fx.mktree(repo, [("100644", "blob", evil_blob, b"main.py")])
    commit_body = subprocess.run(["git", "cat-file", "commit", c], cwd=repo, env=fx.FIXTURE_ENV, capture_output=True).stdout
    fx.swap_loose(repo, c, "commit", commit_body.replace(root_tree.encode(), other_root.encode(), 1))
    case(f"{fmt}_commit_swapped", "REFUSED:object_hash_mismatch/commit", outcome(lambda: cap.build_subject(repo, c)))
    fx.swap_loose(repo, c, "commit", commit_body)

    case(f"{fmt}_type_confusion_blob_as_commit", "REFUSED:object_type_mismatch",
         outcome(lambda: cap.build_subject(repo, b_init)))

# --- object format is taken from the EXPECTED identity, cross-checked with the repository ----
repo1, c1, *_ = fixture(W / "fmt-mismatch", "sha1")
case("expected_sha256_id_against_sha1_repo", "REFUSED:object_format_mismatch",
     outcome(lambda: cap.build_subject(repo1, "a" * 64)))

# --- "hash one read, embed another" reopens the window (why S embeds the verified buffer) ----
repo2, c2, _rt, _pkg, b2 = fixture(W / "double-read", "sha1")
reader = cap.VerifiedObjectReader(repo2, 40)
verified = reader.get(b2, "blob")
reader.close()
fx.swap_loose(repo2, b2, "blob", b'VALUE = "EVIL!!!"\n')
reread = subprocess.run(["git", "cat-file", "blob", b2], cwd=repo2, env=fx.FIXTURE_ENV, capture_output=True).stdout
case("MUTANT_verify_then_reread_embeds_other_bytes", True, verified != reread,
     verified=verified.decode(), reread=reread.decode())

# --- inherited witness (spike PR352#5848970869), RE-EXECUTED on this base: G1/Q consumes the
#     private authority copy after construction, without re-hash (outside Q's ratified domain).
try:
    from app.agent_review import commit_derived_execution_identity_v2 as ident
    from app.agent_review.git_commit_subject_v2 import materialise_commit_subject_v2
    import os
    import zlib

    repo3 = fx.init(W / "g1-witness")
    good, evil = b"VALUE = 'trusted'\n", b"VALUE = 'EVIL!!'\n"
    oid = fx.blob(repo3, good)
    c3 = fx.commit(repo3, fx.mktree(repo3, [("100644", "blob", oid, b"main.py")]))
    M = W / "g1-witness-M"
    materialise_commit_subject_v2(repo_root=repo3, ref=c3, destination=M)
    real = ident.read_commit_blobs_v2

    def swap_then_read(*, repo_root, entries, **kw):
        for p in Path(repo_root).rglob(f"objects/{oid[:2]}/{oid[2:]}"):
            os.chmod(p, 0o644)
            p.write_bytes(zlib.compress(b"blob %d\0" % len(evil) + evil))
        return real(repo_root=repo_root, entries=entries, **kw)

    (M / "main.py").write_bytes(evil)
    ident.read_commit_blobs_v2 = swap_then_read
    try:
        ident.verify_executed_source_identity_v2(repo_root=repo3, commit_sha=c3, subject_root=M, loaded_module_paths=())
        g1 = "SUCCESS"
    except ident.ExecutedSourceIdentityError as exc:
        g1 = "REFUSED:" + exc.reason_code
    finally:
        ident.read_commit_blobs_v2 = real
    case("INHERITED_N1_G1_Q_accepts_EVIL_after_private_copy_swap", "SUCCESS", g1,
         note="witness for S's obligation; violates Q's quiescence precondition, not a Q defect")
except ImportError as exc:  # pragma: no cover
    out["cases"]["INHERITED_N1_G1_Q_accepts_EVIL_after_private_copy_swap"] = {"NOT_TESTED": str(exc)}

out["all_pass"] = all(c.get("pass") for c in out["cases"].values() if "pass" in c)
print(json.dumps(out, indent=1))
