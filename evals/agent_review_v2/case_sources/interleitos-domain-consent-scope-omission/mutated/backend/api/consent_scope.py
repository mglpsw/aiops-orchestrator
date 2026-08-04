"""Cross-axis transition rules for a case record (synthetic, InterLeitos
benchmark fixture).

A case record moves through four independent axes -- clinical,
regulatory, logistics, and communication. A transition directly from the
clinical axis to the communication axis must pass through the regulatory
axis first; skipping it must never be permitted, even though this is a
lower-severity workflow/procedural gap rather than a security boundary.
"""

from __future__ import annotations

AXES = ("clinical", "regulatory", "logistics", "communication")

_ALLOWED_DIRECT_TRANSITIONS = {
    ("clinical", "regulatory"),
    ("regulatory", "logistics"),
    ("regulatory", "communication"),
    ("logistics", "communication"),
    ("clinical", "communication"),
}


def can_transition_axis(current_axis: str, target_axis: str) -> bool:
    """A direct transition is allowed only if it is explicitly listed --
    clinical may never transition directly to communication or logistics
    without passing through regulatory first."""
    return (current_axis, target_axis) in _ALLOWED_DIRECT_TRANSITIONS
