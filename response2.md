# Proposed Solution: "Canonical-Cluster RAG with Confidence-Gated Soft Steering" (CC-RAG)

*Addendum to the low-cost RAG architecture report.*

This is the recommendation for what to actually build: a design that is cheap (~$5–50/month), directly attacks both *duplication* and *ambiguity*, is buildable in about a week, and — because you want this as a research proposal — carries **two specific, testable contributions** rather than just being a recombination of best practices. The framing below is honest about what is novel versus what is off-the-shelf glue, because a defensible research claim depends on that separation.

## The one-sentence idea
Instead of *deleting* duplicates and *hard-filtering* by topic (the two moves in your original design that respectively throw away signal and risk recall collapse), **convert duplication into a useful authority signal** and **convert topic classification into a soft, confidence-gated re-ranking prior** — both inside a single cheap vector store, with no DynamoDB and no OpenSearch.

## The two research contributions

### Contribution 1 — Canonical-cluster ingestion (attacks duplication)
Standard dedup deletes near-duplicates and keeps one copy. That removes the noise but also discards information: *the fact that a passage appears five times across independent sources is itself a corroboration/authority signal.* CC-RAG keeps that signal.

At ingest:
1. Exact content-hash dedup (free, removes literal copies).
2. Near-duplicate clustering via MinHash/LSH (the `datasketch` library) over n-gram shingles, threshold ~0.8, optionally tightened with embedding cosine similarity.
3. Elect one **canonical chunk** per cluster (most recent / longest / most authoritative source).
4. Index **only the canonical chunk** in the vector store, but attach metadata: `cluster_id`, `dup_count`, `source_list`, `first_seen`, `last_seen`.

The payoff is twofold. First, the retrieved context window can never be filled with N copies of the same passage — which is the literal mechanism behind "noisy context" in a duplicated corpus. Second, `dup_count` becomes a feature: a passage corroborated across many sources is weighted up at rerank time, turning redundancy from noise into evidence of reliability.

**Testable claim:** Using `dup_count` as a retrieval-ranking signal yields higher answer correctness than deletion-only dedup, at equal or lower context redundancy.

### Contribution 2 — Confidence-gated soft topic steering (attacks ambiguity without killing recall)
This is the principled fix to the recall-collapse failure mode identified in the main report. The query-time LLM (cheap — Amazon Nova Micro) does **not** emit a single hard topic label that gates the search. It emits a **topic distribution plus a confidence score**. That output then *re-weights* candidate scores instead of excluding anything:

```
final_score = α · vector_similarity
            + β · topic_affinity · query_confidence
            + γ · log(1 + dup_count)
```

The critical safety property is the gate: **when `query_confidence < τ`, set β = 0.** A low-confidence or ambiguous query therefore cannot be mis-routed into the wrong topic bucket — the system degrades gracefully to plain hybrid retrieval. High-confidence queries still get the full precision benefit of topic steering. You keep the precision upside of metadata filtering while removing its recall downside.

**Testable claim:** Confidence-gated soft steering matches the *precision* of hard metadata filtering (your original design) while matching the *recall* of unfiltered hybrid retrieval — i.e., it closes the recall gap that hard filtering opens.

## What's off-the-shelf (keeps it cheap and fast to build)

| Component | Choice | Cost |
|---|---|---|
| Vector store | Amazon S3 Vectors via Bedrock KB (or Aurora pgvector if you want hybrid/BM25) | ~$1–5/mo (S3V) |
| Embeddings | Amazon Titan Text Embeddings V2 (256/512/1024 dims) | per-token, negligible at 2k docs |
| Near-dup clustering | `datasketch` MinHash LSH + embedding cosine | free (your compute) |
| Query topic + confidence | Amazon Nova Micro | ~$0.035 / 1M input tokens |
| Reranker | FlashRank / `ms-marco-MiniLM` cross-encoder (self-hosted) | free |
| Re-weighting + gating | ~50 lines of glue (the "new" part) | free |

The only genuinely new code is the canonical-clustering step and the gated re-weighting function. Everything else is a managed service or a pip install.

## Why this is cheaper than the original design
You eliminate three cost/complexity centers at once: no DynamoDB table + GSIs to maintain, no OpenSearch collection billing ~$350/month while idle, and no per-query LLM call that *gates* (the Nova Micro call is cheap and, under the gate, optional for low-confidence queries). One store, one embedding model, one cheap classifier, one free reranker.

## Evaluation protocol (this is what makes it "research")
A proposal is only a contribution if it's measured against baselines on the same data. Build a small labeled eval set over your 2,000 docs (questions → gold passages; ~100–200 queries, hand-labeled or bootstrapped with an LLM then human-checked).

**Baselines:**
- **B0** — vanilla vector RAG (no dedup, no filtering).
- **B1** — vector + *hard* metadata/topic filter (your original proposed design).
- **B2** — hybrid (BM25 + vector) + cross-encoder rerank (the strong standard baseline).
- **CC-RAG** — the proposed system.

**Metrics:**
- Recall@k, Precision@k, nDCG@k (retrieval quality).
- Answer correctness + faithfulness (via RAGAS).
- **Context redundancy rate** — % of duplicate/near-duplicate tokens in the retrieved context. This directly quantifies the duplication problem you set out to solve, and is the headline metric for Contribution 1.
- **Recall-collapse rate** — fraction of queries where the gold passage is excluded before ranking. This is the headline metric for Contribution 2 (should be ~0 for CC-RAG, meaningfully >0 for B1).

**Hypotheses to confirm:** (H1) CC-RAG has lower context-redundancy rate than B0/B2 without losing recall; (H2) CC-RAG's recall-collapse rate is far below B1's at equal precision; (H3) including `dup_count` improves answer correctness over an ablation that clusters but ignores `dup_count`.

## One-week implementation plan
- **Day 1–2:** Ingest pipeline — hash dedup, MinHash/LSH clustering, canonical election, Titan embeddings, push canonical chunks + metadata into S3 Vectors through a Bedrock Knowledge Base.
- **Day 3:** Query path — Nova Micro (topic distribution + confidence), retrieve top-50 from the KB, FlashRank rerank, apply the gated re-weighting formula.
- **Day 4:** Eval harness — labeled query set + RAGAS, run B0 / B1 / B2 / CC-RAG.
- **Day 5:** Tune α, β, γ, τ on a validation split; run ablations (no `dup_count`; no gate); write up results.

## Honest limitations of the proposal
- The novelty is *modest and incremental*, not a new paradigm: it's a principled recombination with two specific falsifiable claims. Frame the write-up around the measured claims (redundancy reduction, recall-collapse elimination), not around inventing RAG anew.
- S3 Vectors is semantic-only; if Contribution 2's `topic_affinity` term benefits from keyword signals, use the Aurora pgvector backend (hybrid) instead — same architecture, ~$44/month.
- Canonical election ("which copy is authoritative?") is a heuristic and a source of error; make it a tunable, evaluated choice rather than a fixed rule.
- The gate threshold τ and weights α/β/γ are dataset-specific; they must be tuned on held-out data and reported, or the result won't generalize.
