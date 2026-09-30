"""P2d (round 3b, #301 5919204387): mechanical discriminator for the ONE authoritative
process state machine of ARCHITECTURE_FREEZE.md §20 (static, documentary). Nonzero exit on
any failure.

It parses the edge block between the markers `S20-EDGES:BEGIN` / `S20-EDGES:END`:
    STATE <NAME> owns_child=<yes|no|child>
    EDGE  <FROM> -> <TO> : <label>[, <label> ...]
and proves, on the frozen edges:
  L1  OUTCOME has exactly one predecessor, TEARDOWN (EveryOwnedChildPath -> TEARDOWN before
      the final outcome; no HANDSHAKE -> REFUSED / DONE shortcut)
  L2  every reader state reaches OUTCOME (no dead end) and every child-owning state reaches
      TEARDOWN
  L3  the exit classes required by the adjudication all label some edge into the TEARDOWN
      funnel: setup_error, exec_error, unit_deadline, controlled_signal, BaseException,
      normal_completion, transport_failure
  L4  HANDSHAKE observes controlled_signal and unit_deadline (N4, 4148788387)
  L5  the child bootstrap is terminal: its successors are only CHILD_EXECVE / CHILD_EXIT,
      and no child state has an edge into a reader state (N6)
  L6  every state named by an edge is declared, and no undeclared REFUSED/DONE state exists
  L7  the prose law line is present in §20, and §20 contains no "→ REFUSED" shortcut
Each mutant below is applied to the parsed edges in memory and MUST turn a law RED; a
mutant that stays GREEN means the check does not discriminate (the run then fails).
Usage: p2d_state_machine_check.py <ARCHITECTURE_FREEZE.md> <out.json>
"""
import json
import re
import sys

REQUIRED_EXITS = ["setup_error", "exec_error", "unit_deadline", "controlled_signal", "BaseException",
                  "normal_completion", "transport_failure"]
LAW = "EveryOwnedChildPath → TEARDOWN before final outcome"


def parse(text):
    sec = text[text.index("## 20."):text.index("## 21.")]
    block = sec[sec.index("S20-EDGES:BEGIN"):sec.index("S20-EDGES:END")]
    states, edges = {}, []
    for ln in block.splitlines():
        m = re.match(r"^STATE\s+(\w+)\s+owns_child=(yes|no|child)\s*$", ln)
        if m:
            states[m.group(1)] = m.group(2)
            continue
        m = re.match(r"^EDGE\s+(\w+)\s+->\s+(\w+)\s+:\s+(.+?)\s*$", ln)
        if m:
            edges.append((m.group(1), m.group(2), [x.strip() for x in m.group(3).split(",")]))
    return sec, states, edges


def reach(edges, start, stop_at=None):
    seen, todo = {start}, [start]
    while todo:
        cur = todo.pop()
        for a, b, _ in edges:
            if a == cur and b not in seen and b != stop_at:
                seen.add(b)
                todo.append(b)
    return seen


def laws(sec, states, edges):
    out = {}
    preds = {a for a, b, _ in edges if b == "OUTCOME"}
    out["L1_only_teardown_reaches_outcome"] = preds == {"TEARDOWN"}
    reader = [s for s, k in states.items() if k in ("yes", "no") and s != "OUTCOME"]
    owning = [s for s, k in states.items() if k == "yes" and s != "TEARDOWN"]
    out["L2_no_dead_end"] = all("OUTCOME" in reach(edges, s) for s in reader)
    out["L2_owning_reaches_teardown"] = all("TEARDOWN" in reach(edges, s) for s in owning)
    funnel = {a for a, b, _ in edges if b == "TEARDOWN"} | {"TEARDOWN"}
    labels_into_funnel = set()
    for a, b, ls in edges:
        if b in funnel or b == "TEARDOWN":
            labels_into_funnel.update(ls)
    out["L3_required_exits_present"] = all(x in labels_into_funnel for x in REQUIRED_EXITS)
    hs = {x for a, b, ls in edges if a == "HANDSHAKE" for x in ls}
    out["L4_handshake_observes_signal_and_deadline"] = {"controlled_signal", "unit_deadline"} <= hs
    child = [s for s, k in states.items() if k == "child"]
    child_succ = {b for a, b, _ in edges if a in child}
    out["L5_child_bootstrap_terminal"] = bool(child) and child_succ <= {"CHILD_EXECVE", "CHILD_EXIT"} \
        and all(states.get(b) == "child" for b in child_succ)
    named = {a for a, _, _ in edges} | {b for _, b, _ in edges}
    out["L6_states_declared"] = named <= set(states) and not ({"REFUSED", "DONE"} & set(states))
    out["L7_prose_law_and_no_shortcut"] = LAW in sec and "→ REFUSED" not in sec and "-> REFUSED" not in sec
    return out


MUTANTS = {
    "M1_handshake_to_outcome": lambda e: e + [("HANDSHAKE", "OUTCOME", ["setup_error"])],
    "M2_primary_recorded_to_outcome": lambda e: e + [("PRIMARY_RECORDED", "OUTCOME", ["transport_failure"])],
    "M3_handshake_ignores_wakeup": lambda e: [x for x in e if not (x[0] == "HANDSHAKE" and "controlled_signal" in x[2])],
    "M4_child_unwinds_into_reader": lambda e: e + [("CHILD_BOOTSTRAP", "PRIMARY_RECORDED", ["BaseException"])],
    "M5_timeout_dead_end": lambda e: [x for x in e if not (x[0] == "UNIT_TIMEOUT")],
    "M6_undeclared_refused": lambda e: e + [("HANDSHAKE", "REFUSED", ["exec_error"])],
}


def main():
    text = open(sys.argv[1], encoding="utf-8").read()
    sec, states, edges = parse(text)
    res = {"states": states, "edges": len(edges), "laws": laws(sec, states, edges), "mutants": {}}
    fails = [k for k, v in res["laws"].items() if not v]
    if len(edges) < 10 or len(states) < 8:
        fails.append("parse_too_small")
    for name, mut in MUTANTS.items():
        ml = laws(sec, states, mut(list(edges)))
        red = sorted(k for k, v in ml.items() if not v)
        res["mutants"][name] = {"killed": bool(red), "red_laws": red}
        if not red:
            fails.append(name)
    res["verdict"] = "PASS" if not fails else "FAIL"
    res["failures"] = fails
    json.dump(res, open(sys.argv[2], "w"), indent=1)
    for k, v in res["laws"].items():
        print(f"{'PASS' if v else 'FAIL':5} {k}")
    for k, v in res["mutants"].items():
        print(f"{'KILL' if v['killed'] else 'LIVE':5} {k:36} {v['red_laws']}")
    print(f"states={len(states)} edges={len(edges)} failures={fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
