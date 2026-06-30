# Evaluating a Low-Cost RAG Prototype: Metadata Pre-Filtering, DynamoDB, and the Right Vector Store

## TL;DR
- **Your core instinct is right, but two specifics are wrong.** Metadata filtering to combat noise is a well-regarded, literature-backed technique — but (a) DynamoDB is the wrong store for "search topics to find related documents," and (b) a hard LLM topic classifier at query time is the single biggest risk to recall. Use a vector DB's *native* metadata filtering instead of a separate DynamoDB candidate-set step.
- **The cheapest sound architecture is Amazon S3 Vectors behind Bedrock Knowledge Bases (~$1–5/month for 2,000 docs), or Aurora Serverless v2 + pgvector (~$44/month, or near-zero with scale-to-zero) if you need hybrid search.** Both are first-class Bedrock vector stores and both support native metadata filtering, eliminating the need for DynamoDB pre-filtering entirely. OpenSearch Serverless is the expensive baseline you correctly rejected — ~$350/month minimum even when idle.
- **Metadata filtering alone is not sufficient.** To fix duplication and ambiguity, combine: deduplication at ingest (exact hash + MinHash/embedding near-dup), native metadata filtering (soft, not a hard topic gate), and a reranker (Amazon Rerank at $1 per 1,000 queries or a free self-hosted cross-encoder). This combination addresses your actual problem far better than topic-routing into separate candidate sets.

## Key Findings

**1. Metadata filtering is effective and well-supported — but it is a precision tool that trades away recall.** Engineering blogs, vendor docs, and recent papers (arXiv 2510.24402, "Metadata-Driven RAG for Financial QA") agree metadata filtering narrows the search scope and improves precision. But the same literature warns that pre-retrieval filtering is a "double-edged sword": if the filter is wrong, the right document is excluded *before* search ever runs, and no amount of better embeddings or reranking can recover it. Notably, the FinanceBench study found the largest gains came from embedding chunk metadata directly into the text ("contextual chunks") rather than from filtering, and that a reranker was essential for precision.

**2. DynamoDB is a poor fit for "search topics to find related documents."** DynamoDB is a key-value/document store optimized for known-key access patterns. It has no full-text search, no native multi-attribute filtering, and a Query requires an exact partition-key match. To filter by topic you'd need a Global Secondary Index per access pattern (max 20 GSIs per table), and anything beyond 1–2 fields forces a Scan, which reads the whole table and is slow and costly. AWS's own guidance is that for "full-text search or flexible deep querying," you should stream DynamoDB into Amazon OpenSearch via DynamoDB Streams — i.e., DynamoDB is explicitly *not* the tool for this. You'd be re-implementing a worse version of what a vector DB's metadata filter does natively.

**3. Bedrock Knowledge Bases support native metadata filtering across all their vector stores.** This is the decisive fact: you do not need DynamoDB. Bedrock KB supports metadata filtering and, as of 2025–2026, supports these backends: OpenSearch Serverless, OpenSearch Managed Cluster, Aurora PostgreSQL (pgvector), Neptune Analytics, S3 Vectors, Pinecone, MongoDB Atlas, and Redis Enterprise Cloud. (Note: plain RDS PostgreSQL with pgvector is *not* supported — only Aurora PostgreSQL.) Bedrock can also dynamically extract metadata filters from the natural-language query via LLM function calling, which is a softer, better-supported version of your topic-classification idea.

**4. Cost analysis (2,000 docs ≈ tens of thousands of chunks, low query volume):**
- **OpenSearch Serverless (the expensive baseline):** Per AWS's own big-data blog, the minimum redundant deployment is four base half-OCUs at "approximately $350 per month," and a non-redundant dev/test deployment is "$174 per month (two half OCUs)"; a full 4-OCU production config runs ~$700/month at $0.24/OCU-hour. Critically, this floor is billed *even when idle* — and as CloudBurn documents, "deleting a Bedrock Knowledge Base does not delete the OpenSearch Serverless collection it created. That collection keeps billing at roughly $350/month indefinitely… until you manually delete it." This is exactly why you found it too expensive.
- **Amazon S3 Vectors (GA December 2025):** Per the AWS S3 pricing page, customers "pay separately for adding data ($0.20 per GB), storing data ($0.06 per GB/month), and querying data" (query data-processed at ~$0.004/TB on the first tier, working out to roughly $2.50 per million queries at the top tier). For 2,000 docs this is roughly **$1–5/month**. Zero idle cost. Natively integrated with Bedrock KB. **The standout low-cost AWS-native answer.**
- **Aurora Serverless v2 + pgvector:** ~$0.12/ACU-hour; a 0.5-ACU floor ≈ **$44/month**, or scale-to-zero (0 ACU minimum, supported since Nov 2024) drops compute to near-zero when idle (≈15s cold start). First-class Bedrock KB store and ~90% cheaper than OpenSearch Serverless. Best AWS option if you want hybrid (vector + keyword + SQL) search; keep a 0.5-ACU floor if you need warm sub-100ms queries.
- **Pinecone serverless:** Free Starter tier (2 GB storage / ~300K vectors, no credit card) covers a 2,000-doc prototype at **$0**. Paid Standard has a "$50 monthly minimum that's applied to your usage," with storage at $0.33/GB/month. First-class Bedrock KB store.
- **Qdrant:** Free-forever cluster (1 GB RAM, 4 GB disk) covers the prototype; self-host on a small VM ~$80–106/month later. Not a Bedrock KB backend (use in a custom pipeline).
- **Supabase (pgvector):** Free tier (500 MB DB) or Pro $25/month includes the whole backend; pgvector at no extra charge. Great if not locked to Bedrock.
- **Chroma:** Free, embedded/self-hosted; ideal for a local prototype.
- **Weaviate:** Free cloud sandbox / self-host; Flex plan from ~$45/month.

**5. For DUPLICATION specifically:** Deduplicate at ingest, don't try to fix it at query time. The standard pipeline (per the Databricks RAG cookbook and recent LLM-corpus papers): exact content-hash dedup first (cheap), then MinHash/LSH near-duplicate detection (Jaccard similarity over n-gram shingles, threshold ~0.8), optionally embedding-based semantic dedup. Keep the "best" copy (most recent/authoritative). **For AMBIGUITY:** query rewriting, HyDE (hypothetical document embeddings), hybrid search (BM25 + vector via Reciprocal Rank Fusion), and cross-encoder reranking. The consensus across papers and practitioner write-ups: the two-stage "hybrid retrieve → rerank" pipeline dominates single-stage methods — one finance-domain benchmark (arXiv 2604.01733) reports Recall@5 of 0.816 for Hybrid + Cohere Rerank versus 0.695 for hybrid RRF alone.

**6. LLM topic classification at query time is the riskiest part of your design.** It adds roughly 500–1,500 ms of latency and an extra LLM call per query, and — far worse — a misclassification filters out the correct documents entirely (recall collapse). Cheaper, more robust alternatives: a **semantic router** (embedding similarity to reference utterances, ~5–20 ms, near-zero cost) with an LLM fallback for low-confidence queries; or simply let the vector DB's native *soft* metadata filter + reranker do the work. If you keep an LLM classifier, use Amazon Nova Micro ($0.035 per 1M input / $0.14 per 1M output tokens) or Nova Lite — not a frontier model.

## Details

### Is metadata pre-filtering the right technique?
Yes as a concept, with caveats. The technique you describe — extract topic/summary/metadata at ingest, classify the query, filter candidates — is a recognized "pre-retrieval filtering" pattern. The literature is clear on both sides:
- **Strengths:** improves precision, reduces noise, enables access control, scopes large corpora. Unstructured.io, Turso, and multiple practitioner guides endorse it. The AMAQA paper (arXiv 2505.13557) shows explicit metadata filtering is "essential," because serializing metadata into the embedding text instead causes the retriever to "dilute those constraints."
- **Pitfalls:** Pre-retrieval filtering can "inadvertently harm recall by over-constraining the search," and "their value depends heavily on the quality of the controlling LLM" (arXiv 2510.24402). Inconsistent tagging, rigid taxonomies, filter bubbles, cold-start, and documents spanning multiple topics all degrade it. The OptyxStack RAG-reliability guide calls wrong metadata filters "one of the easiest failure modes to miss," because the right document is "filtered out before search starts" while downstream metrics still look reasonable.

The crucial design correction: **filtering should be a soft, native capability of the vector store applied alongside vector similarity, not a hard separate gate that produces a candidate set in a different database.** Your DynamoDB-as-pre-filter design implements the dangerous version (a hard gate). Bedrock KB / pgvector / Pinecone native metadata filters implement the safer version.

### Why DynamoDB is the wrong store here
DynamoDB shines for high-scale, known-key access. Your access pattern ("given a topic, find related documents, possibly matching multiple attributes") is exactly what DynamoDB is bad at:
- Query needs an exact partition key; filtering on non-key attributes happens only *after* a Scan reads the entire table.
- No full-text/semantic search; you can't fuzzy-match topics.
- Multi-attribute filtering needs a GSI per pattern (max 20), and you still can't query >2 fields at once.
- AWS's documented recommendation for this need is to offload to OpenSearch via DynamoDB Streams.

You would be building a brittle, manually maintained topic index that duplicates — worse — the metadata filter your vector DB already provides. Drop it.

### Bedrock integration specifics
- Native metadata filtering is supported on all Bedrock KB vector stores.
- **S3 Vectors with Bedrock KB:** semantic search only (no hybrid), up to ~1 KB custom metadata and ~35 metadata keys per vector, with a strict ~2 KB filterable-metadata cap. Practical tip from AWS Heroes: store dates as `YYYYMMDD` numbers (so `GREATER_THAN_OR_EQUALS` works) and mark the internal `AMAZON_BEDROCK_*` keys non-filterable to avoid frequent `ValidationException` errors.
- **Aurora pgvector with Bedrock KB:** supports hybrid search; requires a GIN index on the metadata `jsonb` column and HNSW iterative scans (pgvector 0.8.0+) so selective filters don't under-return results.

### S3 Vectors — the key low-cost candidate, with honest limitations
S3 Vectors is genuinely cheap and zero-idle-cost, and independent benchmarks show it's accurate for small-to-medium RAG corpora (one evaluation reports Recall@10 of 0.95–1.00 for 100–500 file workloads; another reports ~91% recall at 10M vectors). But know the tradeoffs: it's semantic-only (no hybrid/BM25); latency is "100–800 ms" per Caylent (versus 10–100 ms for OpenSearch), not sub-10 ms; recall hovers 85–90%; and — important — **recall can drop sharply when aggressive metadata filters are applied** (Zilliz reported it can fall below 50% under heavy filtering). For a 2,000-doc prototype with low QPS, these limits are acceptable and the cost savings are enormous. It is GA since December 2025 and supports up to two billion vectors per index.

### Reranking and the recommended retrieval stack
The most reliable quality lever is a reranker. Options and prices:
- **Amazon Rerank 1.0 on Bedrock:** $1.00 per 1,000 queries, where one query = up to 100 document chunks (≤512 tokens each; >100 chunks counts as multiple queries).
- **Cohere Rerank 3.5:** $2.00 per 1,000 searches (one search = one query + up to 100 documents); Rerank 4 Fast ~$2.00/1,000, Rerank 4 Pro ~$2.50/1,000. Available both via Cohere's API and on Bedrock.
- **Free self-hosted:** `cross-encoder/ms-marco-MiniLM` (CPU, ~100–300 ms for 50 candidates), FlashRank (sub-20 ms on CPU, ~4 MB nano model), or `BAAI/bge-reranker-v2-m3` (higher quality, multilingual, Apache 2.0). Zero API cost — you pay only for your own compute.

For a tight budget, a free MiniLM/FlashRank cross-encoder is the right starting point; graduate to Amazon Rerank ($1/1k) if you want a managed, zero-ops option.

### Failure modes of your specific design
1. **Recall collapse from misclassification** — if the query-time LLM mislabels the topic, the correct docs are filtered out entirely. This is the dominant risk and the reason to avoid a hard gate.
2. **Multi-topic documents and multi-intent queries** — a single topic label per doc breaks for documents spanning topics; dense retrieval is "one vector, one intent," so multi-intent queries get mis-routed.
3. **Rigid taxonomy & topic drift** — fixed topic lists go stale as the corpus grows; new content doesn't fit existing buckets.
4. **Maintenance burden** — someone must own and evolve the taxonomy and the DynamoDB GSIs.
5. **Hidden cost/latency** — an extra LLM call per query plus a DynamoDB round-trip before you even hit the vector DB.

## Recommendations

**Stage 0 — Ingest pipeline (do this regardless of store):**
1. Exact-hash dedup, then MinHash/LSH near-duplicate dedup (threshold ~0.8 over n-gram shingles), keeping the most authoritative copy.
2. Use a cheap LLM (Nova Micro/Lite) at *ingest* to extract topic/summary/metadata — store these as **vector metadata fields**, not in a separate DynamoDB table. Optionally prepend a short contextual summary to each chunk ("contextual chunks") before embedding.
3. Semantic chunking; embed with Amazon Titan Text Embeddings V2 (configurable 256/512/1024 dims to tune the cost-quality tradeoff).

**Stage 1 — Cheapest viable prototype:** Bedrock Knowledge Base backed by **Amazon S3 Vectors** (~$1–5/month, zero idle cost). Use **native metadata filtering** as a *soft* filter alongside vector similarity. Add a reranker (free self-hosted MiniLM/FlashRank, or Amazon Rerank at $1/1k queries).

**Stage 2 — If you need hybrid (keyword + vector) or SQL-joined filtering:** switch the Bedrock KB backend to **Aurora Serverless v2 + pgvector** with scale-to-zero (≈$44/month at a 0.5-ACU floor, or near-zero idle). It's a one-config change; the rest of the stack is unchanged.

**Stage 3 — Replace the hard topic classifier:** if you want query-aware routing, use a **semantic router** (embedding similarity, ~5–20 ms) with an LLM fallback only for low-confidence queries, OR Bedrock's dynamic metadata-filter extraction (LLM function-calling). Never let a single classification hard-exclude the corpus.

**Non-AWS budget alternative:** if you're not locked to Bedrock, **Supabase (pgvector, $0–25/month)** or **Pinecone free tier / Qdrant free tier** plus a reranker is equally valid and arguably simpler to operate.

**Benchmarks that change the plan:**
- Move off S3 Vectors when (a) you need hybrid search, (b) sustained QPS pushes latency/recall unacceptably, or (c) *filtered* recall drops below your target — measure Recall@k and precision@k on a labeled query set.
- Move off the Pinecone free tier when you exceed ~300K vectors or trip the $50/month minimum.
- Reconsider OpenSearch Serverless only at high QPS / millions of vectors where its ~$350+/month floor amortizes and you need its hybrid search and ranking features.

## Caveats
- Pricing figures are 2025–2026 list prices (mostly us-east-1) and change frequently; S3 Vectors query cost scales with *data processed* (index size × queries), so re-run the math at your real query volume and growth before committing.
- S3 Vectors is new (GA Dec 2025); its recall-under-filtering and latency profile are less battle-tested than OpenSearch or pgvector. Benchmark with your own data and queries.
- The Bedrock Rerank and Cohere per-search figures are corroborated from reputable secondary sources (Caylent, Bacancy, eesel, OpenRouter) because the live AWS/Cohere pricing pages render these rates only in interactive widgets; verify on the vendor pages before committing. Note Bedrock Rerank was reported as not available in us-east-1.
- Aurora Serverless v2 scale-to-zero incurs a ~15-second cold-start on the first query after idle (longer after 24h paused); keep a 0.5-ACU floor if your prototype needs consistently warm latency.
- "Best copy" selection in deduplication and topic-taxonomy design are judgment calls that need domain input.