"""Benchmark harness for AgentReview v2 (issue #88).

Deliberately separate from `app/agent_review/` (the production engine):
this package only CONSUMES that engine's real, already-merged functions to
run synthetic evaluation cases -- it adds no review logic, no contract, and
no gate authority of its own. See `docs/AGENT_REVIEW_V2_BENCHMARK.md` for
the full scope and the explicit boundary on what this slice does and does
not execute.
"""
