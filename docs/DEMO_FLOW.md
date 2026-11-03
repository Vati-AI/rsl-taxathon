# Five-scenario demo acceptance

`python3 scripts/dev.py serve` presents five synthetic scenario fixtures to unblock UI/contract coding. It does not run the investigation pipeline. The live end-to-end demo is TAX-058, after the preceding implementation tasks.

| Scenario | Required outcome |
|---|---|
| SYN-001 legitimate unusual refund | Reconcile; prepare clearance for officer approval; no automatic payment |
| SYN-002 related-party fees/losses | Test risk and legitimate alternatives; targeted evidence questions |
| SYN-003 employment/consultancy ambiguity | Pause for specialist judgement; no forced classification |
| SYN-004 imported-service trail gap | Explain missing VAT/withholding evidence and request specific records |
| SYN-005 no verified legal route | Record policy-gap review; no invented enforcement conclusion |

TAX-024 expands the minimal fixture summaries into linked documents and expected findings/non-findings. Test duplicate submissions, denied tenant access, unavailable services and malicious document instructions. Record measured timings, citation coverage, false positives and legal-route coverage. Clearly label fixture-only vs live stages.
