"""API schema contract descriptors (synthetic, InterLeitos benchmark fixture).

Declares the fields exposed by the encounter-summary endpoint and the
contract version consumers must match. This module intentionally uses
fabricated, non-clinical placeholder vocabulary (e.g. "condition_code_x")
-- it never represents real patient, professional, or institutional data.
"""

from __future__ import annotations

from dataclasses import dataclass


CONTRACT_VERSION = "3"


@dataclass(frozen=True)
class EncounterSummaryField:
    name: str
    required: bool
    type_name: str


ENCOUNTER_SUMMARY_FIELDS: tuple[EncounterSummaryField, ...] = (
    EncounterSummaryField(name="encounter_id", required=True, type_name="string"),
    EncounterSummaryField(name="condition_code_x", required=True, type_name="string"),
    EncounterSummaryField(name="care_team_ref", required=False, type_name="string"),
)


def field_by_name(name: str) -> EncounterSummaryField | None:
    for candidate in ENCOUNTER_SUMMARY_FIELDS:
        if candidate.name == name:
            return candidate
    return None
