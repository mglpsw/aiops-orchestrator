"""Cross-subsystem primitives with no domain policy of their own.

Nothing in this package encodes AgentReview, CAEM, ProjectOps or Review
Intelligence semantics. It exists so that a discipline several subsystems
independently need -- strict, fail-closed JSON parsing and deterministic
digests -- has exactly one implementation instead of one copy per caller.
"""
