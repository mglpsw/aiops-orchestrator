"""Strict, offline, fail-closed consumer of the CAEM 3.0 F0 interface.

This package is a CONSUMER of CAEM, never a normative writer: it defines no
CAEM schema and no second copy of CAEM policy. `ReasonCode` reuses CAEM's own
`reason_codes` strings verbatim (verified, exhaustively, against the pinned
registry — see `ReasonCode.all()` and `CaemF0LoadResult.reason_codes`); it is
a checked mirror, never an independent AIOps enum. Its job is to verify that
a pinned identity (`config/caem/caem-3.0-f0.pin.json`) matches a locally
available copy of the CAEM 3.0 F0 interface artifact, and to answer strict,
closed questions about which CAEM contracts are available for use.

Scope boundary (AIOps-RI-B0A-1 / issue #119, slice #119.1): pin + quarantine +
loader only. Envelopes, AgentReview/ProjectOps reuse mapping, and the F1
change-request emitter are out of scope here (#119.2-#119.4). Candidate
adoption of any post-F0 revision is a separate, later decision.
"""

from .f0 import (
    CaemF0ContractRecord,
    CaemF0ContractUnavailable,
    CaemF0InterfaceIdentity,
    CaemF0LoadError,
    CaemF0LoadResult,
    CaemF0Pin,
    PinReasonCode,
    ReasonCode,
    load_caem_f0_interface,
    load_caem_f0_pin,
    require_caem_f0_contract,
    scan_active_identity_headers,
)

__all__ = [
    "CaemF0ContractRecord",
    "CaemF0ContractUnavailable",
    "CaemF0InterfaceIdentity",
    "CaemF0LoadError",
    "CaemF0LoadResult",
    "CaemF0Pin",
    "PinReasonCode",
    "ReasonCode",
    "load_caem_f0_interface",
    "load_caem_f0_pin",
    "require_caem_f0_contract",
    "scan_active_identity_headers",
]
