# Aethon — Presentation Flow

Slide-by-slide blueprint for the project presentation. Each slide lists its **goal** (the one thing the audience should take away), the **content** to put on it, a **visual** suggestion, and short **speaker notes**.

**Target:** ~15–18 slides ≈ 15–20 minutes + demo. If time is tight, the merge candidates are marked ⏩.

---

## Before building the deck — checklist

- [ ] Run **Phase 4** of `IMPROVEMENTS.md` (deploy → upload `scripts/generate_test_corpus.py` corpus → batch-run `testset.json`) to get the real numbers for Slide 12. All `[…]` placeholders below come from the eval page's summary line / CSV export.
- [ ] Take screenshots while doing that: upload page showing `duplicates skipped: 1`, chat answer with the router tags (`→ Data Retention Policy Germany · 0.82`), eval side-by-side comparison, eval history table + summary line.
- [ ] Keep one *live* browser tab logged into the frontend if you want a live demo instead of screenshots (Slide 13).

**Design ground rules:** one message per slide; numbers big (48pt+), text small; dark text on light background projects better; every metric slide shows *unfiltered → filtered* in the same order so the audience learns to read it once.

---

## Slide 1 — Title

**Goal:** Set the stage in five seconds.

**Content:**
- Project name: **Aethon**
- Tagline: *"Cutting noisy context out of enterprise RAG with metadata-filtered retrieval"*
- Your name, course/university, date
- Small AWS + LLM logos if you like (it signals the stack instantly)

**Visual:** Clean title slide; optionally a faint background graphic of documents converging into a funnel.

**Speaker notes:** One sentence: "This is a working prototype on AWS that measurably reduces retrieval noise in RAG systems — and I'll show you the numbers."

---

## Slide 2 — The problem: enterprise corpora are noisy

**Goal:** Make the audience *feel* the problem before any tech appears.

**Content:**
- Enterprise document stores are full of:
  - **Duplicates** — the same policy uploaded five times
  - **Near-duplicates** — same document, different country / year / version
  - **Ambiguity** — "the data retention policy"… which of the three?
- Concrete hook example (use the demo corpus!):
  > "How long must customer records be retained?"
  > Germany says **10 years** · France says **5 years** · Spain says **6 years** — *same document, different facts.*

**Visual:** Three nearly identical document icons labeled DE / FR / ES with one highlighted differing line each.

**Speaker notes:** This is not an artificial problem — every company with multi-jurisdiction operations, versioned handbooks, or shared drives has exactly this corpus shape.

---

## Slide 3 — Why plain RAG fails here

**Goal:** Explain the failure mechanism, not just the symptom.

**Content:**
- How standard RAG retrieves: embed question → cosine-similar chunks → top-k into the LLM
- The catch: **near-duplicate chunks are near-identical vectors.** Similarity search *cannot* tell them apart — boilerplate dominates the embedding.
- Consequences:
  - Top-k fills with chunks from the *wrong* versions → contradictory context
  - The LLM hedges, mixes facts, or answers confidently from the wrong document
  - Tokens are wasted on redundant text
- Frame it: *"The ranker is being asked to make a distinction the vector space doesn't contain."*

**Visual:** Diagram: question → vector store → top-5 chunks colored by source doc (3 different colors mixed) → confused LLM. Keep the same color coding for the rest of the deck.

**Speaker notes:** Emphasize this is a *structural* problem of similarity search, not a tuning problem — that's what motivates adding a different signal.

---

## Slide 4 — The core idea: route first, then retrieve

**Goal:** The one-slide summary of the whole thesis. If people remember one slide, it's this.

**Content:**
- Aethon inserts a **cheap metadata pre-filter in front of the vector store**:

  1. **Route** — decide *which documents* the question is about (metadata index)
  2. **Retrieve** — vector search *only within* those documents (KB metadata filter on `document_id`)
  3. **Answer** — LLM sees a small, clean, single-source context

- Slogan: *"Don't retrieve from everything and hope the ranker sorts it out — pick the right documents first."*

**Visual:** The 3-step pipeline as a horizontal flow; show the funnel narrowing from N documents → 1–3 documents → 5 chunks.

**Speaker notes:** The metadata filter on the Bedrock Knowledge Base is the research contribution; everything else in the talk is scaffolding to build and *measure* it.

---

## Slide 5 — Research questions / hypotheses

**Goal:** Make the project measurable — this earns the "research" label.

**Content:** State 3–4 testable claims:
- **H1 (Precision):** Filtering increases the share of retrieved context from the *correct* document
- **H2 (Answer quality):** Cleaner context → more factually correct answers
- **H3 (Efficiency):** Fewer/cleaner chunks → fewer context tokens, no latency penalty
- **H4 (Dedup):** Exact duplicates can be eliminated at ingestion for zero LLM/vector cost

**Visual:** Four numbered cards. You'll revisit them with ✓/✗ on the results slide — set that expectation now ("we'll come back to these").

**Speaker notes:** Each hypothesis maps to a metric you'll show later: context precision, judge accuracy, tokens/latency, skipped-duplicate count.

---

## Slide 6 — System architecture

**Goal:** Show it's a real, complete, running system — without drowning the audience.

**Content:**
- Serverless, event-driven pipeline on AWS (eu-central-1), IaC with Terraform, CI/CD via GitHub Actions OIDC
- **Ingestion path:** Upload (presigned S3) → Textract OCR → dedup check → LLM metadata extraction → DynamoDB index + S3 sidecar → Bedrock KB ingestion → S3 Vectors
- **Query path:** Question → DynamoDB router → filtered Bedrock retrieve → LLM answer
- Components: 6 Lambdas, 2 DynamoDB tables, API Gateway, static S3 frontend

**Visual:** Simplify the README's architecture diagram: two horizontal lanes ("ingest" top, "query" bottom) meeting at the Knowledge Base. Color the router + metadata filter in an accent color — they're the contribution.

**Speaker notes:** Don't read every box. Point to the two accent-colored boxes: "everything else is standard plumbing; these two are the idea."

---

## Slide 7 — Ingestion: turning PDFs into a metadata index ⏩ *(mergeable into 6)*

**Goal:** Show where the routing metadata comes from.

**Content:**
- Textract OCR → raw text
- LLM extracts structured metadata per document: `{title, summary, topics[], facts[], document_type}`
- Stored twice, for two purposes:
  - **DynamoDB** → the router's search index
  - **S3 sidecar `metadata.json`** → attached to every chunk in the vector store (this is what makes filtered retrieval possible)
- Show a real extracted-metadata JSON snippet from a demo doc (facts like "retention period 10 years", "authority BfDI")

**Visual:** One document flowing into the two stores; the JSON snippet as a code block.

**Speaker notes:** The extracted `facts` are exactly the discriminative bits that separate near-duplicates — this feeds the next slide.

---

## Slide 8 — The router: IDF-weighted metadata matching

**Goal:** Explain the smart part in one formula + one intuition.

**Content:**
- Question terms are matched against each document's metadata terms
- Scoring:

  `score(doc) = Σ (t ∈ Q∩D) log(1 + N/df(t)) / √|D|`

- **The intuition (say it in words):** near-duplicates share almost all terms; only discriminative terms — *germany*, *2026*, *v2* — are rare across the corpus. IDF weighting makes exactly those terms decide the routing.
- Confidence margin: candidates below **75% of the top score** are dropped → wrong near-duplicates don't ride along; evenly-scored candidates (genuinely ambiguous questions) all stay
- Safety net: no candidates → fall back to unfiltered retrieval (no recall collapse)
- Explainable: every answer shows *which* documents were selected, their scores, and the matched terms

**Visual:** Worked example with real numbers: question "retention in **Germany**?" → term table showing common terms (idf ≈ 0.9 each) vs *germany* (idf ≈ 1.8) → DE doc 5.7, FR doc 3.9 (68% → dropped). Screenshot of the chat router tags below.

**Speaker notes:** This is a BM25-inspired heuristic, deliberately simple and cheap — the point is that even a simple router beats no router; a learned/embedding router is future work.

---

## Slide 9 — Free win: duplicate elimination at ingestion

**Goal:** The efficiency story — short and punchy.

**Content:**
- SHA-256 fingerprint of the normalized OCR text, checked **before** the LLM call
- A duplicate costs:
  - **0** LLM extraction tokens
  - **0** embeddings / vector storage
  - **0** noise at query time — it never enters the Knowledge Base
- Marked `SKIPPED_DUPLICATE` with a `duplicate_of` pointer (auditable, visible in the UI)

**Visual:** Two-column "with / without dedup" cost path, or the upload-page screenshot showing `duplicates skipped: 1`.

**Speaker notes:** Exact-hash only — near-duplicate *detection* is future work; near-duplicate *disambiguation* is what the router already does.

---

## Slide 10 — Evaluation methodology

**Goal:** Establish that the results are credible and honestly measured.

**Content:**
- Built-in A/B harness: every eval question runs through **both pipelines concurrently** — filtered vs. unfiltered, same corpus, same LLM, same top-k
- Metrics per run:
  - **Context precision** — % of retrieved chunks from the expected document (1 − precision = wrong-document noise)
  - **Hit rate & MRR** — is the right document found, and does it rank first?
  - **Unique source documents** — how many documents the context mixes (label-free noise signal)
  - **Answer accuracy** — a **blind LLM judge** grades both answers against a reference: answers anonymized as A/B in *random order* (no position/name bias)
  - Context tokens + latency per pipeline
- Labeled test set of 10 questions over a controlled corpus

**Visual:** Small diagram: one question forking into two pipelines, metrics collected on both sides, judge at the bottom comparing both answers to the reference.

**Speaker notes:** Call out the blinding — examiners like hearing that you anticipated judge bias. Also mention runs are stored (DynamoDB) and exportable (CSV) for reproducibility.

---

## Slide 11 — The controlled test corpus

**Goal:** Show the experiment is *designed*, not cherry-picked.

**Content:**
- Synthetic corpus generator reproduces the three failure modes on purpose:
  - **3 near-duplicate policies** — identical boilerplate, different facts (DE 10y / FR 5y / ES 6y, different authorities, fines, deadlines)
  - **1 exact duplicate** — byte-identical German policy, different filename → dedup demo
  - **2 handbook versions** — 2023 vs 2026, conflicting vacation/remote/probation rules
- 10 labeled questions, each with expected document + reference answer
- Reproducible: `python3 scripts/generate_test_corpus.py` — zero dependencies

**Visual:** Grid of the 6 documents with their one differing fact highlighted; the duplicate crossed out with a "SKIPPED" stamp.

**Speaker notes:** "Because the noise is controlled, we know exactly what a correct retrieval looks like for every question — that's what makes precision and the judge meaningful."

---

## Slide 12 — Results ★ THE KEY SLIDE

**Goal:** The payoff. Big numbers, unfiltered → filtered.

**Content:** Fill from the eval summary + CSV after the Phase 4 batch run. Suggested layout — four big stat tiles + a small table:

| Metric | Unfiltered | **Filtered (Aethon)** |
|---|---|---|
| Context precision | `[…]%` | **`[…]%`** |
| Answer accuracy (LLM judge) | `[…]%` | **`[…]%`** |
| Correct-document hit rate | `[…]%` | **`[…]%`** |
| MRR | `[…]` | **`[…]`** |
| Avg. unique source docs per answer | `[…]` | **`[…]`** |
| Avg. context tokens | `[…]` | **`[…]`** (−`[…]%`) |
| Avg. latency | `[…]s` | **`[…]s`** |

- Revisit Slide 5's hypotheses with ✓/✗
- Plus the dedup result: "1 of 6 uploads auto-skipped as duplicate — zero LLM/vector cost"

**Visual:** Stat tiles for the top 2 metrics (precision, answer accuracy), the rest in the compact table. Green highlight on the filtered column.

**Speaker notes:** If a metric *doesn't* improve (e.g. latency roughly equal), say so plainly and explain why it's acceptable — honesty here buys credibility for the wins. If unfiltered hit rate is already high, emphasize precision/MRR: "finding the document somewhere in top-k isn't the problem — *not drowning it in wrong-document chunks* is."

---

## Slide 13 — Demo / screenshots

**Goal:** Prove it's real and working.

**Content (pick live demo OR 3 screenshots):**
1. **Upload page** — corpus uploaded, `duplicates skipped: 1` in the sync line
2. **Chat** — ask the Germany-retention question; point at the router tags: filter applied, selected doc + score, 1 source doc, then the correct "10 years" answer
3. **Eval page** — side-by-side comparison: unfiltered mixing 3 documents vs filtered at 100% precision; the history table after the batch run

**Visual:** Full-bleed screenshots, one per build/click. Zoom into the router tag strip — it's small on screen but it *is* the explainability story.

**Speaker notes:** If demoing live, pre-warm everything (Lambdas cold-start, KB synced, eval history cleared then batch pre-run). Have the screenshots as backup slides in case the venue network fails.

---

## Slide 14 — Engineering under constraints ⏩ *(mergeable into 6 or cuttable)*

**Goal:** The pragmatic-engineering story — good for applied-degree audiences.

**Content:**
- Student-budget constraint drove real architecture decisions:
  - Vector store: OpenSearch Serverless ≈ **$350/month idle** → migrated to **S3 Vectors ≈ $1–5/month**
  - DynamoDB scan router: fine at prototype scale (~2k docs), deliberately not a search engine
  - LLM via OpenAI-compatible external API (swappable via one Terraform variable)
- Everything serverless & pay-per-request → idle cost near zero
- Full IaC: Terraform + GitHub Actions OIDC (no long-lived AWS keys)

**Visual:** Cost comparison bar (350 vs ~3 $/month) — it's a fun, memorable chart.

**Speaker notes:** "Constraints are the point: the thesis is that noise reduction is achievable with *cheap* components — the filter idea doesn't require expensive infrastructure."

---

## Slide 15 — Limitations & future work

**Goal:** Show you know the boundaries — pre-empt the obvious questions.

**Content:**
- **Limitations:**
  - Exact-hash dedup only (rewordings slip through)
  - Term-based router: no stemming/synonyms; English stop-word list; scan doesn't scale past ~10k docs
  - LLM judge is itself an LLM (mitigated by blinding + randomization, not eliminated)
  - API is unauthenticated (prototype)
- **Future work:**
  - Embedding-based / hybrid router — compare against the IDF router *with the same eval harness*
  - Near-duplicate detection (MinHash / metadata-embedding similarity)
  - Larger, real-world corpus evaluation
  - Auth (Cognito) + multi-tenant metadata scoping

**Visual:** Two columns. Keep each item ≤ 6 words on the slide; elaborate verbally.

**Speaker notes:** The strongest line: "the eval harness is the lasting contribution — any future router plugs into the same A/B measurement."

---

## Slide 16 — Conclusion

**Goal:** Three sentences they'll repeat to someone else.

**Content:**
- Noisy, duplicated corpora are a **structural** failure mode of vector-only RAG
- A **cheap metadata router + KB filter** fixes it measurably: precision `[…]→[…]%`, answer accuracy `[…]→[…]%`, tokens −`[…]%` *(fill from Slide 12)*
- Built end-to-end, serverless, ~$5/month, fully reproducible (IaC + corpus generator + eval harness)
- **Thank you — questions?** (repo link / QR code)

**Visual:** The three takeaways as large text; QR code to the GitHub repo bottom-right.

**Speaker notes:** Stop talking after "questions?" — resist the urge to add "…and one more thing."

---

## Backup slides (have ready, don't present)

- **B1 — Scoring formula deep-dive:** the worked IDF example from Slide 8 with the full arithmetic; why √|D| normalization; why threshold 0.75 (near-dups missing a discriminative term score ~0.55–0.69 of top).
- **B2 — Judge prompt & bias controls:** the actual system prompt, verdict taxonomy, order randomization, correct=1/partial=0.5 scoring.
- **B3 — DynamoDB document schema:** one real item JSON with the status state machine (`RECEIVED → TEXTRACT_* → METADATA_EXTRACTED → SYNCED` / `SKIPPED_DUPLICATE`).
- **B4 — Fallback behavior:** what happens on ambiguous/short questions (unfiltered fallback, `metadata_filter_applied: false`) — answers the "what if the router is wrong?" question.
- **B5 — Cost breakdown:** per-service monthly estimate; per-question cost (retrieval + LLM tokens).
- **B6 — Full architecture diagram** (the detailed README version) for deep technical questions.

---

## Suggested timing (20-minute slot)

| Slides | Section | Time |
|---|---|---|
| 1–3 | Problem | 4 min |
| 4–5 | Idea + hypotheses | 3 min |
| 6–9 | System (architecture, router, dedup) | 5 min |
| 10–12 | Evaluation + results | 5 min |
| 13 | Demo | 2 min |
| 14–16 | Constraints, limits, conclusion | 1–2 min |

Cut order if overrunning: 14 → 7 (merge into 6) → shorten 13 to one screenshot.
