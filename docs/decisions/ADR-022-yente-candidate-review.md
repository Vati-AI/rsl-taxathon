# ADR-022: yente candidate review and corroborated confirmation

Status: proposed (TAX-037, pending review by Keletso as integration area owner)
Owner and task: Leseli (owner), TAX-037; implemented by leseli-claude-20261105-037
Date: 2026-11-05

Context and smallest prototype need: yente (TAX-027) proposes candidate entities for a name. The contract `MatchCandidate` (ADR-003) carries `candidate_id`, `dataset`, `dataset_version`, `score`, `review_status` and `reviewed_by`, and says the score is never identity, wrongdoing or legal authority. AGENTS.md requires corroboration for match candidates. TAX-036 maps parties to FtM entities, and TAX-053, TAX-054 and TAX-074 will read reviewed matches. There is no Vati API server yet, so the adapter must be usable as a library.

Decision:

- `integration/yente/candidate_review.py` uses only the standard library and calls only the locked routes `GET /catalog` and `POST /match/{dataset}`.
- **Dataset version:** every candidate records the dataset name and the version from `/catalog`. A dataset whose `index_version` differs from its manifest `version`, or that isn't in `current`, is refused (`upstream_unavailable`, reason `index_stale`), so no candidate is recorded against a version yente isn't serving.
- **Queries:**
  - One query per call, because yente discourages batching.
  - Only name, country, registration number, incorporation date and birth date are sent, with the property names the locked route recommends per schema.
  - Tax and ID numbers stay inside Vati and are compared locally.
- **Normalized candidate:** yente's ID, caption, schema and properties are kept unchanged. The score becomes a 4-decimal string. The record also says whether the candidate was above the threshold, gives the threshold and the retrieval time, and adds a local corroboration map (`agrees`, `conflicts` or `absent`) for registration, ID and tax numbers, birth date and country. A set with more than one candidate above the threshold is `ambiguous`.
- **Review decisions:**
  - Outcomes are `confirmed`, `rejected` and `ambiguous`. `ambiguous` means the officer cannot tell, and maps to contract `unreviewed`.
  - Each decision records the officer UUID and role, the rationale (required), cited case evidence (it must belong to the case and be readable by the reviewer), its basis, the score at review, the dataset version and the time.
  - Decisions are append-only, and the latest per (entity, candidate, version) is current.
  - Idempotency keys make retries safe.
  - Reviewer roles equal `FINDING_REVIEW` in the identity policy (auditor, specialist, supervisor). Service identities and taxpayers are refused.
- **Confirmation rules (never on score alone):**
  - It needs an agreeing identifier or cited case evidence; otherwise it's refused as `score_only`.
  - Name and alias similarity never corroborate, because they're what the score measures. Country agreement is recorded but isn't enough.
  - Any conflicting identifier blocks confirmation (`identifier_conflict`).
  - In an ambiguous set, an identifier-based confirmation must rest on an identifier the other candidates above the threshold don't also agree on (`ambiguous_candidates`). Otherwise the officer cites evidence.
  - Only one candidate per entity and version may be confirmed at a time (`already_confirmed`). Changing the choice means rejecting the first, and history is kept.
- **Bound to a version:** reviewing candidates from an older version than yente now serves is a `version_conflict`. After a re-index, candidates are matched again and start `unreviewed`. Earlier decisions stay as history.
- **No merge:** a confirmation produces a `MatchLink` (Vati entity ID, yente candidate ID, dataset, version, decision, basis). Neither the Vati FtM entity nor yente's entity is changed, and nothing is written to yente.

Alternatives and tradeoff:

- Treat `match: true` (score at or above the threshold) as a match. It's simple but makes a similarity score into identity, which the contract and AGENTS.md forbid. The TAX-027 smoke test shows two different firms both above the threshold.
- Send tax and ID numbers to yente for better scoring. That would improve recall, but put sensitive identifiers into a service without authentication. Local comparison keeps them inside Vati, at the cost of yente scoring on fewer properties.
- Use yente's `exclude_entity_ids` to hide rejected candidates. That gives cleaner results, but the rejection would no longer be visible on the next match. Rejected candidates are shown with their decision instead.
- Carry decisions across dataset versions. That means less re-review, but a new version can change a candidate's properties, so a confirmation is only valid for the version it was made on.

Official sources and version/date checked (2026-11-05): yente at commit `6580a2aae2b662559d75301f06820bb0b8f7a122` (integration/upstreams.lock.json, version 5.5.0):
- `yente/routers/admin.py`: `GET /catalog` returns `DataCatalogModel` with `datasets`, `current` and `outdated`.
- `yente/data/dataset.py`: `YenteDatasetModel` has `version`, `index_version`, `load` and `index_current`.
- `yente/routers/match.py`: `POST /match/{dataset}` with `threshold`, `limit` and a deprecated `cutoff` capped at the threshold, so results below the threshold aren't returned. Batching is discouraged, and the recommended properties are listed per schema.
- `yente/data/common.py`: `EntityMatchResponse`, `EntityMatches` and `ScoredEntityResponse` (`id`, `caption`, `schema`, `properties`, `datasets`, `score`, `match`).
- `yente/settings.py`: `SCORE_THRESHOLD = 0.70`, `MATCH_PAGE = 5`, `MAX_BATCH = 100`.
- Observed behaviour: the TAX-027 smoke results in integration/yente/README.md.

Verification performed:
- 21 tests in `tests/integration/test_yente_candidate_review.py`, run against a fake of the locked routes that returns the recorded TAX-027 results for the committed synthetic dataset. They cover:
  - the alias match ("SBR Trading" at score 1.0) and its normalization;
  - contract validity of `MatchCandidate`;
  - score-only refusal;
  - a false match with a conflicting registration number;
  - the ambiguous Highland pair: an ambiguous decision, then confirmation with evidence, and one decided by a distinguishing registration number;
  - one confirmation per entity;
  - unchanged original identities on both sides;
  - version binding and a stale index;
  - reviewer roles equal to the identity policy;
  - rationale, officer, evidence and idempotency checks;
  - upstream error mapping.
- Not performed: a run against the live yente stack in this session.

Consequences and revisit trigger:
- The Vati API (TAX-054 pipeline) stores `ReviewLedger` decisions and exposes `MatchCandidate` on `Entity`. TAX-074 builds ownership graphs only on confirmed links.
- Revisit when:
  - yente is bumped (recheck the routes and defaults);
  - licensed datasets are added (their terms and versions);
  - a Vati persistence layer replaces the in-memory ledger;
  - officers need to confirm against more than one dataset at once.
