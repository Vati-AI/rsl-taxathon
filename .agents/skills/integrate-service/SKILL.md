---
name: integrate-service
description: Build or verify a Vati adapter against pinned Jube, Frappe, OpenAleph, yente or Docling APIs.
---

# Integrate Service

Read docs/SERVICE_SETUP.md, docs/API_CONTRACTS.md and integration/upstreams.lock.json. Fetch only official locked source with scripts/upstreams.py. Inspect its supported deployment and API specification before implementing. Keep vendor databases independent and ports isolated. Use synthetic evidence; verify authentication, timeout, idempotency, source/version mapping and tenant scope. Record service revision and a real smoke result. Fixture success is not live integration success. Do not auto-download licensed datasets or transmit private evidence to external models.
