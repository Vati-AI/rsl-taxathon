# Frontend design standard

User direction: both interfaces should feel like polished Frappe applications. This is an implementation and review requirement for every frontend task, not a claim that the current starter screens meet it.

## Reference and components

Use the official [Frappe UI desktop and component examples](https://ui.frappe.io/) as the visual reference (reviewed 2026-11-03). Use Vue 3/TypeScript and supported Frappe UI components for custom screens per ADR-002; verify and pin the package version before adoption. Native Frappe pages should reuse framework form/list patterns. A custom portal does not inherit the Desk appearance automatically. Do not switch frameworks merely to imitate a screenshot: record the component choice in TAX-020/021 and reuse one shared visual contract.

Leseli leads the shared component showcase in TAX-020; Kananelo contributes taxpayer forms and validates the same contract in TAX-021. Both can start independently from the defaults below. Jane owns visual/accessibility checks in TAX-023 and subsequent UI QA. Shared-file changes must be coordinated through a focused PR; consuming screens reuse those components and tokens.

## Visual contract

These are Vati defaults inspired by the reference, not asserted Frappe token values. Implement once in shared styles/tokens; any change must update both interfaces and the showcase.

- Neutral light workspace: canvas #f8fafc, white surfaces, #e2e8f0 borders, #0f172a primary text and #475569 supporting text. Restrained #1d4ed8 action accent. Verify contrast in actual rendered states.
- System sans-serif font; 14px desktop UI, 16px taxpayer form text, 20–24px page titles, 1.5 body line height. Use a small documented type scale. Long names and numbers must remain readable.
- Spacing scale 4, 8, 12, 16, 24, 32px; 6–8px control/surface radii; subtle dividers. Shadows only where they clarify layering, such as popovers.
- Persistent desktop sidebar, clear active item, compact page header, title/breadcrumbs, and one visually dominant primary action. Smaller screens use accessible collapsed navigation.
- Officer lists are task-focused tables with meaningful columns, search/filter/sort, aligned headers, right-aligned numeric values and predictable pagination. No decorative charts or invented metrics. Taxpayer pages use clearer step-by-step forms with more breathing room.
- One outline icon family, consistent sizes, visible text labels for primary actions, accessible names for icon-only controls. Status badges use text plus colour; risk is a review signal, never a verdict.
- No decorative gradients, glass panels, neon/glowing effects, emoji navigation, oversized hero sections, excessive rounded cards, arbitrary animations or one-off screen styling.

## First reviewable screens

Before expanding the screen inventory, submit a working component showcase and representative screens in TAX-020/021: auditor case queue and case detail; taxpayer submission form and submission status. Include realistic synthetic long names, amounts, dates, missing values and error messages. These first PRs establish the visual baseline; later screens must follow it. Review can request changes before merge. Jane prepares the harness independently and captures approved screenshot baselines after the first screens are accepted.

The showcase includes buttons, form fields, validation, tabs, table/filter controls, badges, dialogs and notification patterns, with normal, hover, focus, disabled, loading and error examples. Use working synthetic flows; label fixture data clearly. An unavailable action is disabled with a reason, not a button that silently does nothing.

## Visual acceptance for every UI PR

Attach screenshots of the changed screens at 1440px, 1024px and 390px widths and identify the tested browser. Include loading, empty, error, success and denied-access states relevant to the change. Record keyboard navigation, visible focus, labels, readable contrast and zoom/reflow checks. Automated accessibility and screenshot comparison support, but do not replace, human review.

No clipped text, unintended page-wide horizontal scrolling, overlapping controls or inconsistent spacing. Wide officer tables may scroll inside a labelled region; taxpayer forms must reflow. Use actionable error text that preserves entered values. Confirmations and consequential decisions must preserve existing human-approval requirements.

Jane records visual defects with screenshots and reproduction steps. The human frontend owner reviews alignment with this standard and approves the first-screen baseline; agents do not mark visual approval as passed on their own. Functional checks alone do not satisfy visual acceptance. Subsequent UI PRs link the baseline and explain intentional deviations. Human review and merge remain mandatory.
