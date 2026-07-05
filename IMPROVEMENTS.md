# Aethon — Improvement Plan & Status

Working document for the evaluation + relevance/efficiency upgrade. **Update the Status column as steps complete** so work can be resumed across sessions.

Status legend: ⬜ not started · 🔨 in progress · ✅ done (code) · 🚀 deployed/verified

---

## Why these changes

The presentation needs to prove one claim: **metadata-filtered retrieval beats plain vector RAG on noisy corpora (duplicates, near-duplicates, ambiguous documents).** The current eval only shows document-level *hit rate* and a synthetic token-reduction number. That neither measures *noise* directly nor proves the *answers* get better. And the current router (raw term-overlap) treats boilerplate terms and discriminative terms equally — exactly the wrong behavior for near-duplicate corpora.

### Evaluation upgrade (the presentation centerpiece)

| New metric | What it proves |
|---|---|
| **Context precision** (fraction of retrieved chunks from the expected document) | How much *wrong-document noise* reaches the LLM. The headline metric for the noise problem. |
| **Distractor chunks** (chunks retrieved from other documents) | The noise, counted directly. |
| **MRR / first-relevant rank** | Whether the right document ranks *first*, not just "somewhere in top-k". |
| **Unique source documents per retrieval** | Label-free noise indicator: unfiltered pulls chunks from N docs, filtered from 1–2. |
| **LLM-as-judge answer correctness** (blind, order-randomized, vs. a reference answer) | End-to-end proof: filtering produces *more correct answers*, not just cleaner context. |
| **Latency per pipeline** | Filtering doesn't slow queries down (smaller context can even speed up generation). |

Plus: **batch test-set runner** (one click runs a whole labeled question suite) and a **synthetic ambiguous-corpus generator** (same policy for different countries + exact duplicates + versioned near-duplicates) so the demo runs on controlled, explainable noise.

### Pipeline relevance & efficiency

1. **IDF-weighted router scoring.** Near-duplicate documents share most terms; only a few discriminative terms (country, year, version) separate them. Raw overlap counts boilerplate equally. IDF weighting makes rare terms dominate — the router disambiguates duplicates by exactly the fields that distinguish them. Length normalization stops long-metadata docs from always winning; a relative confidence margin drops weak candidates instead of padding the filter to top-k.
2. **Router explainability.** Query response returns the selected documents with titles, scores, and matched terms — shown in the chat UI. (Also removes the misleading `potential_context_token_reduction_percent` from `/query`, which compared against a theoretical upper bound rather than a real baseline; the honest comparison lives in `/eval`.)
3. **Ingestion-time exact-duplicate detection.** SHA-256 of the normalized extracted text; a duplicate is detected *before* the LLM metadata call and never ingested — saving the extraction call, the embedding cost, and keeping redundant copies out of the vector store entirely.
4. **Warm document-index cache** in the query Lambda (TTL) — cuts DynamoDB scan latency/cost per query.

---

## Steps

### Phase 1 — Evaluation upgrade

| # | Step | Files | Status |
|---|------|-------|--------|
| 1.1 | Retrieval metrics in eval Lambda: context precision, first-relevant rank + MRR, distractor chunks, unique source docs, retrieval/answer/total latency (ms) | `Lambda_src/eval/app.py` | ✅ |
| 1.2 | LLM-as-judge: optional `expected_answer` in `POST /eval`; single blind, order-randomized judge call grades both answers (correct / partially_correct / incorrect + reason); graceful degradation on judge failure; bounded timeouts (answers 15s, judge 8s) to stay inside the 28s Lambda cap | `Lambda_src/eval/app.py` | ✅ |
| 1.3 | Accept `expected_document_filename` as an alternative to `expected_document_id` (resolved against DynamoDB) so labeled test sets are portable across re-uploads | `Lambda_src/eval/app.py` | ✅ |
| 1.4 | Extended summary aggregates (avg precision, MRR, unique docs, judged answer accuracy, avg latency per pipeline), backward-compatible with old records | `Lambda_src/eval/app.py` | ✅ |
| 1.5 | Eval dashboard: expected-answer field, ID-or-filename field, new metric tags on the comparison columns, new history-table columns, extended CSV export | `Frontend/eval.html`, `Frontend/app.js` | ✅ |
| 1.6 | Batch test-set runner on the eval page: upload a `testset.json`, runs each question sequentially with progress, then refreshes the summary | `Frontend/eval.html`, `Frontend/app.js` | ✅ |

### Phase 2 — Pipeline relevance & efficiency

| # | Step | Files | Status |
|---|------|-------|--------|
| 2.1 | IDF-weighted, length-normalized candidate scoring + relative confidence margin (`RELATIVE_SCORE_THRESHOLD`, default 0.75) — implemented identically in query and eval Lambdas | `Lambda_src/query/app.py`, `Lambda_src/eval/app.py` | ✅ |
| 2.2 | Router explainability: `/query` returns `selected_documents` (id, title, score, matched terms); chat UI shows them; replace the misleading upper-bound reduction metric with `unique_source_documents` | `Lambda_src/query/app.py`, `Frontend/app.js` | ✅ |
| 2.3 | Warm-invocation document-index cache in query Lambda (`DOCUMENTS_CACHE_TTL_SECONDS`, default 45) | `Lambda_src/query/app.py` | ✅ |
| 2.4 | Exact-duplicate detection in result_processor: normalized-text SHA-256 stored per document; duplicates marked `DUPLICATE` / `SKIPPED_DUPLICATE` with `duplicate_of`, skipping LLM extraction, sidecar, and KB ingestion. Needs `dynamodb:Scan` for the result_processor role | `Lambda_src/result_processor/app.py`, `Infrastructure/iam.tf` | ✅ |
| 2.5 | Upload page shows skipped duplicates in the sync status line | `Frontend/app.js` | ✅ |

### Phase 3 — Demo assets & docs

| # | Step | Files | Status |
|---|------|-------|--------|
| 3.1 | Synthetic test-corpus generator: zero-dependency script producing PDF variants of the same policy for different countries (near-duplicates with different facts), one exact duplicate (dedup demo), two versions of an employee handbook (version ambiguity), plus a labeled `testset.json` for the batch runner | `scripts/generate_test_corpus.py` | ✅ |
| 3.2 | README: rewrite eval section (new metrics, judge, batch runner, corpus generator), document router scoring + dedup + cache, add scripts/ to layout | `README.md` | ✅ |
| 3.3 | Local sanity checks: `py_compile` all Lambdas, unit-test scoring/judge-parsing/dedup-hash logic, validate generated PDFs | scratchpad | ✅ |

### Phase 4 — Deploy & verify (NEXT — start here tomorrow)

| # | Step | How | Status |
|---|------|-----|--------|
| 4.1 | Deploy | Commit + push to `main`; GitHub Actions applies Terraform (Lambda zips + the iam.tf change). Or locally: `cd Infrastructure && terraform init -backend-config=backend.hcl && terraform apply` | ⬜ |
| 4.2 | Generate + upload demo corpus | `python3 scripts/generate_test_corpus.py --out ./corpus` → upload the PDFs via the frontend → wait for metadata → **Sync Knowledge Base**. Expect `germany_data_retention_policy_copy.pdf` to end up `SKIPPED_DUPLICATE` (upload it a minute after the original so the original's hash is already stored) | ⬜ |
| 4.3 | Run the batch test set | Eval page → "Run test set" → select `corpus/testset.json`. Verify precision / MRR / judge / unique-docs metrics populate and filtered beats unfiltered | ⬜ |
| 4.4 | Presentation numbers | Export CSV; headline numbers: filtered vs unfiltered **context precision**, **answer accuracy**, **hit rate**, **unique source docs**, avg **token reduction** | ⬜ |

### Phase 5 — Ideas / not started (optional, post-presentation)

- Near-duplicate detection (MinHash / embedding similarity on metadata) — catches "same doc, reworded", not just byte-identical content.
- Embedding-based router (embed question + doc summaries, cosine match) as a stronger alternative to term IDF; compare all three in eval.
- Auth (Cognito/JWT) on the API before any non-demo exposure.
- Async batch eval endpoint (Step Functions or self-invoking Lambda) to escape the 29s API Gateway cap for big test suites.

---

## Design decisions log

- **Judge is blind and order-randomized** (answers presented as A/B in random order, pipeline names hidden) to avoid position/name bias. Verdict scoring for the accuracy aggregate: correct = 1, partially_correct = 0.5, incorrect = 0.
- **Judge runs inside the same eval invocation** with an 8s timeout and graceful `judge.error` fallback — the run still records retrieval metrics if judging fails. Kept simple instead of an async judging pipeline.
- **Duplicates are skipped, not ingested.** The efficiency claim *is* that redundant copies never reach the LLM/vector store. Near-duplicates (different facts) still ingest — they're the ambiguity problem the filter solves at query time.
- **Exact-hash dedup only** (normalized text SHA-256). Near-dup detection is future work (Phase 5).
- **`/query` no longer reports `potential_context_token_reduction_percent`** — it compared actual tokens against `MAX_RETRIEVAL_RESULTS × chunk_max_tokens`, a theoretical bound, not a measured baseline. Real comparisons come from `/eval`.
- **Query/eval candidate-selection code is intentionally duplicated** (no shared layer at prototype scale). If you change `_select_candidate_documents` / `_question_terms` / `_document_terms` in one Lambda, mirror it in the other.
- **Scoring formula:** `score(doc) = Σ_{t ∈ Q∩D} log(1 + N/df(t)) / sqrt(|D|)` over eligible (synced) docs; keep top-k candidates with `score ≥ 0.75 × top_score`. BM25-inspired, explainable in one presentation slide. The 0.75 margin was chosen empirically: near-duplicates missing a discriminative term score ~0.55–0.69 of the top match; equally relevant candidates score ~1.0.

## Resume notes

- All Phase 1–3 code is written and syntax-checked locally; **nothing is deployed yet** — start at Phase 4.
- New env-var knobs (all optional, defaults in code, no Terraform changes needed): `RELATIVE_SCORE_THRESHOLD` (query+eval), `DOCUMENTS_CACHE_TTL_SECONDS` (query), `LLM_ANSWER_TIMEOUT_SECONDS` / `LLM_JUDGE_TIMEOUT_SECONDS` (eval).
- The only Terraform change is `dynamodb:Scan` added to the result_processor role in `Infrastructure/iam.tf`.
- Old eval records in DynamoDB lack the new fields; the summary/CSV/table code handles them (`–` placeholders). Consider **Clear history** before the presentation runs so aggregates only reflect the new metrics.
