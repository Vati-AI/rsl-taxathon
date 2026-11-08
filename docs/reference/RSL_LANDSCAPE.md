# RSL systems landscape (public research, unverified)

Collected 2026-11-07 for [ADR-043](../decisions/ADR-043-vati-downstream-of-rsl-selection.md).
These are summaries of web search results. The pages themselves could not be fetched from the
development environment. Vendor case studies are the vendor's own claims. Nothing here is
confirmed by RSL. Confirm every point before any live integration.

## What RSL already runs

| System | What it reportedly does | Source |
|---|---|---|
| e-Tax (e-filing) | CIT, income tax, VAT and PAYE returns; amendments; balances and filing history | [RSL guide on online services][guide], [RSL e-taxation guide][efiling] |
| e-Payments | Online payment by mobile money or bank; receipts and history | [e-Payments][epay] |
| Online Refunds Application (October 2025) | Claims of M20 to M1,000 with no issues go to "Processing"; problems show "Pending"; larger claims needing checks show "Audit in progress"; incomplete claims are rejected or cancelled with reasons on the dashboard; tax debts are offset automatically | [The Reporter][refunds], [Lesotho Times][lestimes] |
| Refund guidance (older) | A refund claim triggers an audit before payment; payment within 10 working days of audit completion; VAT refund originals submitted physically for endorsement | [RSL tax refunds][refundpdf], [RSL tax refund page][refundpage] |
| Lekuka / IDMS | National e-invoicing through accredited billing systems; QR verification; self-service portal; data warehouse that pre-fills returns and feeds risk and audit tools; mandatory deadline extended to about 30 October 2026 | [NRD case study][nrd], [Zmayet Law][zmayet], [SNI][sni] |
| Risk-based audit system (Paradox Box) | 120+ risk criteria in 6 risk sets; daily referrals from the Risk Unit to the Audit Unit; VAT and comprehensive audit workflows between team leader, auditors and taxpayers; 80 audit worksheets; integrated with PSRM | [Paradox Box cases][paradox] (vendor claim) |
| Oracle PSRM / ETPM | Core tax administration system; business intelligence and data warehouse procurement | [MesTech][mestech] |
| RSL refund-fraud model | Data science solution to detect fraudulent VAT refunds | [RSL Annual Report 2024][ar2024] |
| WSO2 Integrator, API Manager, Identity Server | Central integration layer, more than 200,000 transactions a month, including TIN and VAT-registration checks | [WSO2 case study][wso2] |
| Transfer pricing capacity | TIWB (UNDP/OECD) and ATAF support for audits; a 2026 High Court ruling limits imputation under s113 | [TIWB statement][tiwb], [Afriwise][afriwise] |
| Plans | 2026/27 business plan includes digitising VAT refunds between Lesotho and South Africa | [RSL Business Plan 2026-27][bp] |

## Where Vati overlaps and how ADR-043 resolves it

| Vati component | Overlaps | Resolution |
|---|---|---|
| Claim form, invoice entry, submissions list | Online Refunds Application, Lekuka | Retired or reframed (TAX-125, TAX-129) |
| Jube as case engine; Vati risk rules | Risk-based audit system, RSL fraud model | Investigation sub-case of an RSL case; corroboration, not scoring (TAX-131) |
| Refund lanes and undisputed release | Online Refunds statuses, PSRM payment and offset | Recommendation for claims in audit (TAX-132) |
| Frappe login | WSO2 Identity Server | TAX-117, TAX-136 |

## Gaps Vati fills

- The investigation after selection. That means assembling Lekuka, ASYCUDA, books and related
  parties into a cited case with alternative explanations.
- Reasons behind a score.
- Digital, itemised evidence requests and responses, replacing physical originals.
- Release of the undisputed part of a claim.
- Multinational and related-party analysis, with a legal-route and policy-gap gate.

[guide]: https://www.rsl.org.ls/sites/default/files/2024-08/Guide%20on%20Online%20Services.pdf
[efiling]: http://www.rsl.org.ls/sites/default/files/2025-02/Guide%20on%20E-Taxation%20Platform%20(E-Filing).pdf
[epay]: https://epayment.rsl.org.ls/login
[refunds]: https://www.thereporter.co.ls/2025/10/26/rsl-launches-online-refunds-application-system/
[lestimes]: https://lestimes.com/rsl-enhances-digital-transformation/
[refundpdf]: https://www.rsl.org.ls/sites/default/files/2024-05/Tax%20Refunds_0.pdf
[refundpage]: https://www.rsl.org.ls/tax-refund
[nrd]: https://www.nrdcompanies.com/case-studies/improving-vat-collection-in-lesotho-through-the-e-invoicing-platform/
[zmayet]: https://zmayetlaw.co.ls/lekuka-e-invoicing-rsl-extends-the-mandatory-deadline-to-30-october-2026-but-the-regulations-have-not-moved/
[sni]: https://snitechnology.net/lesotho-e-invoicing/
[paradox]: https://www.paradoxbox.org/cases
[mestech]: https://www.mestech.co.ls/?p=1828
[ar2024]: https://www.rsl.org.ls/sites/default/files/2025-02/Annual%20report%202024.pdf
[wso2]: https://wso2.com/customers/revenue-services-lesotho-seamlessly-scales-transactions-monthly-with-wso2/
[tiwb]: https://www.rsl.org.ls/sites/default/files/2025-06/Press%20Statement%20-%20Tax%20Inspectors%20Without%20Borders%20%28TIWB%29%20Mission%20in%20Collaboration%20with%20UNDP%20and%20OECD.pdf
[afriwise]: https://www.afriwise.com/blog/transfer-pricing-lesotho-high-court-confirms-limit-to-revenue-services-lesothos-power-to-impute-income-under-section-113
[bp]: https://www.rsl.org.ls/sites/default/files/2026-07/Business%20Plan%20Summary%20Version%20-%202026-2027.pdf
