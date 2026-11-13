"""Portal settings for RSL's own systems and the Online Refunds simulator (TAX-125, ADR-043).

Pure functions over the site configuration, so they are testable without a bench.

- ``vati_online_refunds_simulator``: ``true`` only on development, test and demo sites.
  Taxpayers file refund claims in RSL's Online Refunds Application, not in Vati, so the
  portal's claim form (TAX-043/044) is kept only as a labelled simulator of that system
  for fixtures and demonstrations. Unset or anything other than ``true``: the simulator
  and its methods are not available.
- ``vati_rsl_links``: ``{"online_refunds_dashboard": "https://..."}``, the RSL page where a
  taxpayer sees their claims. Vati does not know RSL's URLs; without configuration the
  portal links nowhere and says where to look instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vati_tax.portal import submissions
from vati_tax.submissions import domain

SIMULATOR_KEY = domain.SIMULATOR_KEY
LINKS_KEY = "vati_rsl_links"


def simulator_enabled(conf: Mapping[str, Any]) -> bool:
    """Whether this site offers the fixture-only Online Refunds simulator."""
    return domain.simulator_enabled(conf)


def online_refunds_url(conf: Mapping[str, Any]) -> str | None:
    """The configured link to RSL's Online Refunds dashboard (https only), or none."""
    links = conf.get(LINKS_KEY)
    if not isinstance(links, Mapping):
        return None
    return submissions.dashboard_url(links.get("online_refunds_dashboard"))
