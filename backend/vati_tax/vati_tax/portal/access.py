"""Taxpayer portal session boundary rules.

Pure functions with no Frappe import so they are testable without a bench. The
Frappe page guard (``vati_tax/www/taxpayer.py``) and the whitelisted portal API
apply these rules to the live session.

TAX-029 narrows the shell to principals holding a taxpayer membership; the
portal API additionally filters records to the caller's own taxpayer.
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

    outcome: Literal["allow", "login", "deny"]
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


def portal_access(user: str | None, path: str | None, *, is_taxpayer: bool) -> PortalAccess:
    """Decide whether ``user`` may load the portal page at ``path``.

    Guests (or a missing session) are sent to login. Authenticated users need an
    enabled taxpayer membership (TAX-029); officers and service identities are
    denied rather than shown an empty taxpayer shell.
    """
    if not user or user == GUEST_USER:
        return PortalAccess("login", login_location(path))
    if not is_taxpayer:
        return PortalAccess("deny")
    return PortalAccess("allow")
