# ADR-044 "before" screenshots

These were captured on 2026-11-08 with Playwright (Chromium) at 1440×900 against the fixture
build (`python3 scripts/dev.py serve` after `npm run build:auditor`).

| Screen | Height at 1440×900 |
|---|---|
| Overview, queue, case detail, integrity | 1.0 viewport |
| Review and decide | 1.3 viewports (1,185px) |
| Management, as supervisor | 1.9 viewports (1,743px) |
| Component showcase | 2.9 viewports (2,654px) |

The taxpayer app needs a running Frappe site; without one it shows "We could not open the
portal", so it was not measured. TAX-144 records the full inventory and adds the automated
check.
