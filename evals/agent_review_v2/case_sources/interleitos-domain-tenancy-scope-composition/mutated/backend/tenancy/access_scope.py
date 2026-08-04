"""Tenant access-scope resolution (synthetic, InterLeitos benchmark fixture).

Resolves whether a user may access a resource belonging to a given
tenant. Access is granted only through an explicit, direct link between
the user and that exact tenant -- composing access through unrelated
indirect link types (e.g. a shared department, a shared shift, a shared
communication channel) must never be treated as equivalent to a direct
grant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TenantLink:
    tenant_id: str
    link_type: str  # "direct" | "department" | "shift" | "communication"


def has_direct_access(user_links: tuple[TenantLink, ...], resource_tenant_id: str) -> bool:
    """A user may access a tenant's resource only via an explicit
    `direct` link to that exact tenant."""
    return any(
        link.tenant_id == resource_tenant_id and link.link_type == "direct"
        for link in user_links
    )


def resolve_effective_scope(user_links: tuple[TenantLink, ...], resource_tenant_id: str) -> bool:
    """The effective access decision for a resource belonging to
    `resource_tenant_id`."""
    return any(link.tenant_id == resource_tenant_id for link in user_links)
