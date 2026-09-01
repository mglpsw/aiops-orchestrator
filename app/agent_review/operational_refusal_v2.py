"""Derivable operational-refusal family for the AgentReview v2 product
boundary (`#200-G4`, ported with revalidation from `#200-F`/`#277`).

## What this replaces

`#276` gave its CLI a hand-maintained tuple of every owner exception class it
knew about. That tuple needed a third extension after two bounded correction
rounds, and the "complete by construction" test written to stop the recurrence
was itself green while an ordinary `--delivery-id` value leaked a raw
traceback the control structurally could not see. The enumeration method does
not constitute authority: the CLI cannot be the place that knows the full set
of ways the semantic layer is allowed to say no.

## The invariant

An owner declares that a failure is an *expected operational refusal* by
inheriting this marker. The boundary then catches structurally::

    try:
        semantic_operation(...)
    except ExpectedOperationalRefusalV2 as exc:
        emit_structured_refusal(exc.reason_code)

Adding a new legitimate refusal is an owner-local edit. The boundary never
changes, so it cannot fall behind.

## The deliberate negative

Membership is a *declaration*, not a property discovered by sniffing. Anything
not carrying the marker -- `TypeError`, `AttributeError`, `KeyError`, and in
particular `pydantic.ValidationError` -- is a **programmer defect** and escapes
raw. That is the point, not an oversight:

* caller material that is merely invalid must never reach the semantic layer
  at all. It is validated at ingress, *before* the subject is sealed, and
  converted there into a typed member of this family;
* anything malformed that appears *after* the seal is a defect in this
  codebase, and hiding it behind a tidy reason code would make the product
  lie about its own health.

Duck-typing on ``hasattr(exc, "reason_code")`` was rejected for the same
reason: it would silently promote an unrelated third-party exception that
happens to carry the attribute into a first-class product outcome.

## Provenance note (`#200-G4`)

`#277` round 2 proved the *pattern* sound for the sources it covered (the nine
scalar CLI flags, and later ``--profile``/``--grouping-policy`` document
content) but left several other caller-controlled sources -- the
``--responses`` directory and its individual entries, the inner-control-fd
environment variable, and the argparse usage-error path -- still able to leak
a raw exception, a filesystem path, or caller bytes to stderr. This module is
ported unchanged: the gap `#200-G4` closes is coverage
(`operational_ingress_v2.py`, `operational_workspace_v2.py`,
`scripts/aiops-review-run-v2.py`), not this family marker.
"""

from __future__ import annotations

__all__ = ["ExpectedOperationalRefusalV2"]


class ExpectedOperationalRefusalV2(Exception):
    """Marker base for a refusal AgentReview v2 is *designed* to produce.

    Deliberately introduces no ``__init__``. Owners keep their own
    constructor and their own specific ``reason_code``; mixing this class in
    inserts a no-op link into the MRO and changes no existing behaviour. In
    particular the historical ``ValueError`` / ``RuntimeError`` bases are
    retained by every owner, so existing ``pytest.raises(ValueError)`` call
    sites and existing ``except ValueError`` handlers keep working.

    Reason codes are **not** collapsed into a single generic value. The
    boundary catches one family and reports the specific member.
    """

    #: Every member publishes a stable, content-free reason code. Declared for
    #: type checkers and readers; each owner assigns it in its own __init__.
    reason_code: str
