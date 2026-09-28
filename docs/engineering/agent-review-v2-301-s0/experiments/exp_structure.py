"""EXP-STRUCT: S represents every distinction contract A/A2 requires, and refuses the rest.

usage: python -I -S exp_structure.py <toolrepo_source_root> <scratch_dir>
Oracles: (1) an EXPLICITLY declared expected node map, written independently of any serializer;
(2) C3's own enumeration (list_commit_tree_structure_v2 + read_commit_blobs_v2).
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SRC = Path(sys.argv[1]).resolve()
sys.path.insert(1, str(SRC))
W = Path(sys.argv[2]).resolve()

import s0_capture as cap  # noqa: E402

def admitted(**kw):
    """Explicit admission policy (decision (d)): 255 = C3's enumeration default, stated, never implied."""
    return cap.Budget(max_component_len=255, **kw)

import s0_fixture as fx  # noqa: E402

out = {"cases": {}}


def jsonable(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "backslashreplace")
    if isinstance(v, dict):
        return {jsonable(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": jsonable(expected), "observed": jsonable(observed),
                          "pass": expected == observed, **jsonable(extra)}


def outcome(fn):
    try:
        fn()
        return "ACCEPTED"
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


repo = fx.init(W / "struct")
B = {name: fx.blob(repo, data) for name, data in {
    "main": b'VALUE = "main"\n', "init": b'VALUE = "pkg"\n', "mod": b'VALUE = "mod"\n',
    "sh": b"#!/bin/sh\necho s0\n", "lnk": b"pkg", "lnk_up": b"../outside", "lnk_other": b"main.py"}.items()}
EMPTY = fx.empty_tree(repo)


def tree_of(spec):
    return fx.mktree(repo, spec)


pkg = tree_of([("100644", "blob", B["init"], b"__init__.py"), ("100644", "blob", B["mod"], b"mod.py")])
ns2 = tree_of([("040000", "tree", EMPTY, b"sub")])
BASE = [("100644", "blob", B["main"], b"main.py"), ("040000", "tree", pkg, b"pkg"),
        ("040000", "tree", EMPTY, b"ns"), ("040000", "tree", ns2, b"ns2"),
        ("100755", "blob", B["sh"], b"run.sh"), ("120000", "blob", B["lnk"], b"link"),
        ("120000", "blob", B["lnk_up"], b"link-up")]
base_commit = fx.commit(repo, tree_of(BASE))

# (1) declared oracle, independent of the serializer
DECLARED = {
    b"main.py": ("regular", b'VALUE = "main"\n'), b"pkg": ("tree", b""),
    b"pkg/__init__.py": ("regular", b'VALUE = "pkg"\n'), b"pkg/mod.py": ("regular", b'VALUE = "mod"\n'),
    b"ns": ("tree", b""), b"ns2": ("tree", b""), b"ns2/sub": ("tree", b""),
    b"run.sh": ("executable", b"#!/bin/sh\necho s0\n"), b"link": ("symlink", b"pkg"),
    b"link-up": ("symlink", b"../outside"),
}
s = cap.build_subject(repo, base_commit, budget=admitted())
observed = {p: (k, v) for p, (k, _o, v) in s.nodes.items()}
case("parity_with_explicitly_declared_tree", DECLARED, observed)

# (2) parity with the C3 authority's own enumeration
from app.agent_review.git_commit_subject_v2 import list_commit_tree_structure_v2, read_commit_blobs_v2  # noqa: E402

entries = {e.path.encode(): e for e in list_commit_tree_structure_v2(repo_root=repo, commit_sha=base_commit)}
carrier = read_commit_blobs_v2(repo_root=repo, entries=[e for e in entries.values() if e.object_type != "tree"])
c3 = {p: (("tree", b"") if e.object_type == "tree" else
          ("symlink" if e.mode == "120000" else "executable" if e.mode == "100755" else "regular", bytes(carrier[e.path])))
      for p, e in entries.items()}
carrier.close()
case("parity_with_C3_enumeration_and_blobs", c3, observed)

data_a = cap.serialize(s.algorithm, s.commit, s.root_tree, s.nodes)
data_b = cap.serialize(*(lambda t: (t.algorithm, t.commit, t.root_tree, t.nodes))(cap.build_subject(repo, base_commit, budget=admitted())))
case("deterministic_bytes_two_builds", True, data_a == data_b)
alg, com, rt, parsed = cap.parse(data_a)
case("parse_inverse_of_serialize", True, (alg, com, rt, parsed) == (s.algorithm, s.commit, s.root_tree, s.nodes))


# (3) countermodels: each variant differs from BASE in exactly one material distinction
def replace(name, new):
    return [e for e in BASE if e[3] != name] + ([new] if new else [])


VARIANTS = {
    "empty_dir_removed": replace(b"ns", None),
    "empty_dir_added": BASE + [("040000", "tree", EMPTY, b"extra-empty")],
    "nested_empty_dir_removed": replace(b"ns2", ("040000", "tree", EMPTY, b"ns2")),
    "exec_bit_cleared": replace(b"run.sh", ("100644", "blob", B["sh"], b"run.sh")),
    "symlink_target_changed": replace(b"link", ("120000", "blob", B["lnk_other"], b"link")),
    "symlink_to_regular_same_bytes": replace(b"link", ("100644", "blob", B["lnk"], b"link")),
    "empty_dir_to_empty_file": replace(b"ns", ("100644", "blob", fx.blob(repo, b""), b"ns")),
}


def lossy(nodes):
    """Mutant projection: flattened leaves only, exec bit dropped, symlink read as file (the C1/C2 shape)."""
    return {p: v for p, (k, _o, v) in nodes.items() if k != "tree"}


base_digest = hashlib.sha256(data_a).hexdigest()
for name, spec in VARIANTS.items():
    v = cap.build_subject(repo, fx.commit(repo, tree_of(spec)), budget=admitted())
    vd = hashlib.sha256(cap.serialize(v.algorithm, v.commit, v.root_tree, v.nodes)).hexdigest()
    node_diff = sorted(set(v.nodes.items()) ^ set(s.nodes.items()), key=lambda x: x[0])
    case(f"countermodel_{name}_distinguished", True, vd != base_digest and bool(node_diff),
         lossy_mutant_collides=lossy(v.nodes) == lossy(s.nodes))

# (4) explicit refusals, never silent skips
gitlink = fx.git(repo, "rev-parse", base_commit)
case("gitlink_refused", "REFUSED:unsupported_mode",
     outcome(lambda: cap.build_subject(repo, fx.commit(repo, fx.raw_tree(repo, [("160000", gitlink, b"sub")])), budget=admitted())))
case("noncanonical_mode_100664_refused", "REFUSED:unsupported_mode",
     outcome(lambda: cap.build_subject(repo, fx.commit(repo, fx.raw_tree(repo, [("100664", B["main"], b"a.py")])), budget=admitted())))
case("dotdot_name_refused_by_C3_rule", "REFUSED:tree_unrepresentable",
     outcome(lambda: cap.build_subject(repo, fx.commit(repo, fx.raw_tree(repo, [("100644", B["main"], b"..")])), budget=admitted())))
case("duplicate_name_refused", "REFUSED:tree_duplicate_name",
     outcome(lambda: cap.build_subject(repo, fx.commit(repo, fx.raw_tree(
         repo, [("100644", B["main"], b"a.py"), ("100644", B["mod"], b"a.py")])), budget=admitted())))

# raw (non-UTF-8) names: S keeps bytes; record what the C3 authority does with the same commit
raw_commit = fx.commit(repo, fx.mktree(repo, [("100644", "blob", B["main"], b"caf\xe9.py")]))
raw_s = outcome(lambda: cap.build_subject(repo, raw_commit, budget=admitted()))
try:
    c3_raw = [e.path for e in list_commit_tree_structure_v2(repo_root=repo, commit_sha=raw_commit)]
    c3_outcome = "ACCEPTED:" + repr(c3_raw)
except Exception as exc:  # noqa: BLE001 -- recording the authority's behaviour, whatever it is
    c3_outcome = "REFUSED:" + type(exc).__name__ + ":" + str(getattr(exc, "reason_code", exc))[:80]
out["cases"]["non_utf8_name_S_vs_C3"] = {"S": raw_s, "C3": c3_outcome,
                                          "note": "divergence here is a finding: S must adopt C3's rule, not a second one"}

# (4b) decision (d): the component limit is an explicit admission parameter; C3 parity is claimed
#      only when S_G and C3 are given the SAME limit
long_name = b"n" * 253 + b".py"  # 256 bytes
c_long = fx.commit(repo, fx.mktree(repo, [("100644", "blob", B["main"], long_name)]))


def c3_outcome(limit):
    try:
        return "ACCEPTED:" + ",".join(e.path for e in list_commit_tree_structure_v2(
            repo_root=repo, commit_sha=c_long, max_component_len=limit))
    except Exception as exc:  # noqa: BLE001 -- recording the authority's behaviour
        return "REFUSED:" + str(getattr(exc, "reason_code", type(exc).__name__))


def sg_outcome(limit):
    try:
        v = cap.build_subject(repo, c_long, budget=cap.Budget(max_component_len=limit))
        return "ACCEPTED:" + ",".join(p.decode() for p in sorted(v.nodes))
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


case("component_256_limit_300_admitted", True, sg_outcome(300).startswith("ACCEPTED"))
case("component_256_limit_255_refused", "REFUSED:budget_component_len", sg_outcome(255))
case("C3_parity_under_same_limit_300", c3_outcome(300), sg_outcome(300),
     note="parity claim available: same admission limit")
case("C3_also_refuses_under_same_limit_255", True, c3_outcome(255).startswith("REFUSED"),
     note="refusal parity under the same limit")
case("no_admission_limit_refused", "REFUSED:admission_limit_missing",
     outcome(lambda: cap.build_subject(repo, base_commit, budget=cap.Budget())))

# (5) container integrity is part of the digest: truncation / trailing / reorder refused by parse
case("container_truncated_refused", "REFUSED:container_truncated", outcome(lambda: cap.parse(data_a[:-3])))
case("container_trailing_refused", "REFUSED:container_trailing", outcome(lambda: cap.parse(data_a + b"\0")))

out["all_pass"] = all(c.get("pass") for c in out["cases"].values() if "pass" in c)
out["lossy_mutant_collisions"] = sum(bool(c.get("lossy_mutant_collides")) for c in out["cases"].values())
print(json.dumps(out, indent=1))
