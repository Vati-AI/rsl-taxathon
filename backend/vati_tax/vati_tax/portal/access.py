"""Taxpayer portal session boundary rules.

Pure functions with no Frappe import so they are testable without a bench. The
Frappe page guard (``vati_tax/www/taxpayer.py``) and the whitelisted portal API
apply these rules to the live session.

Role and tenant checks are deliberately not invented here: TAX-029 defines the
taxpayer role and tenant permissions and extends ``portal_access``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode, urlsplit

PORTAL_ROOT = "/taxpayer"
GUEST_USER = "Guest"
LOGIN_PATH = "/login"


@dataclass(frozen=True)
class PortalAccess:
    """Outcome of a portal access check."""

    outcome: Literal["allow", "login"]
    location: str | None = None


def portal_path(path: str | None) -> str:
    """Return ``path`` if it is inside the portal, otherwise the portal root.

    Only a same-site path under ``/taxpayer`` survives, so a crafted value can
    never turn the post-login redirect into an off-site or Desk redirect.
    Query strings and fragments are dropped.
    """
    if not path:
        return PORTAL_ROOT
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or "\\" in path:
        return PORTAL_ROOT
    clean = parts.path
    segments = clean.split("/")
    if ".." in segments or "." in segments or "//" in clean:
        return PORTAL_ROOT
    if clean == PORTAL_ROOT or clean.startswith(PORTAL_ROOT + "/"):
        return clean
    return PORTAL_ROOT


def login_location(path: str | None) -> str:
    """Login URL that returns the user to the requested portal page."""
    return f"{LOGIN_PATH}?{urlencode({'redirect-to': portal_path(path)})}"


def portal_access(user: str | None, path: str | None) -> PortalAccess:
    """Decide whether ``user`` may load the portal page at ``path``.

    Guests (or a missing session) are sent to login. Any authenticated user is
    allowed into the shell; data endpoints still only return the caller's own
    records. TAX-029 narrows this to the taxpayer role and tenant scope.
    """
    if not user or user == GUEST_USER:
        return PortalAccess("login", login_location(path))
    return PortalAccess("allow")
