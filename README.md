# Aethon

**Metadata-filtered Retrieval-Augmented Generation (RAG) on AWS — a prototype for cutting noisy context out of enterprise document search.**

Aethon is a university research project that explores a practical answer to a well-known failure mode of enterprise RAG systems: when a knowledge base is full of **duplicated, redundant, or ambiguous documents**, plain vector similarity search pulls back a soup of near-identical or conflicting chunks. The generator then either hedges, contradicts itself, or confidently answers from the wrong document.

Aethon's core idea is to put a cheap **metadata pre-filter in front of the vector store**: before retrieving, it uses a lightweight metadata index to decide *which documents a question is even about*, and then restricts the vector search to those documents. Less noise reaches the LLM, the answer is grounded in the right source, and the context window (and token bill) shrinks.

---

## The problem this solves

Real enterprise corpora are messy in ways that hurt naive RAG:

- **Duplication** — the same policy or manual exists in a dozen slightly different versions.
- **Ambiguity** — the "same" document differs by jurisdiction (e.g. the same law in different countries, with different facts), by year, or by business unit.
- **Redundancy / noise** — boilerplate, near-duplicate paragraphs, and low-signal pages dominate similarity search.

Pure semantic similarity has no notion of *"these two chunks look alike but belong to different documents that must not be mixed."* Aethon adds that notion explicitly, as a metadata filter over a per-document index.

## The core idea

```
Question ─▶ [1] Metadata pre-filter          [2] Filtered vector retrieval      [3] Grounded answer
             (DynamoDB term match)      ─▶     (Bedrock KB, filtered by      ─▶   (LLM over a small,
             picks candidate doc IDs           document_id metadata)              clean context set)
```

Instead of "retrieve from everything, hope the ranker sorts it out," Aethon does "figure out the handful of relevant documents first, then retrieve only within them." The `document_id` metadata filter on the Bedrock Knowledge Base is the research contribution; everything else is scaffolding to make it measurable.

> **Prototype scope.** This is a research prototype, not a production system. Components are deliberately chosen to be cheap and simple (DynamoDB as the metadata index, term-overlap matching instead of a real search engine, S3 Vectors instead of OpenSearch). They are good enough to demonstrate and *measure* the effect — see [Measuring the effect](#measuring-the-effect-the-eval-harness).

> **Work in progress.** Current improvement work (better evaluation metrics, smarter document routing, duplicate detection) is planned and tracked step-by-step in [IMPROVEMENTS.md](IMPROVEMENTS.md).

---

## Architecture

Aethon is a fully serverless, event-driven pipeline on AWS. Everything is provisioned with Terraform and deployed through GitHub Actions.

```
                        ┌─────────────┐
   Browser (static S3)  │  Frontend   │  upload · chat · eval dashboards
                        └──────┬──────┘
                               │  HTTP API (API Gateway)
        ┌──────────────────────┼───────────────────────────────────────┐
        │                      │                                        │
   POST /uploads          POST /query                        POST/GET /sync   POST/GET/DELETE /eval
        │                      │                                        │            │
        ▼                      ▼                                        ▼            ▼
  ┌───────────┐          ┌───────────┐                          ┌───────────┐  ┌───────────┐
  │upload_url │          │  query    │                          │ kb_sync   │  │  eval     │
  └─────┬─────┘          └─────┬─────┘                          └─────┬─────┘  └─────┬─────┘
        │ presigned PUT        │                                      │              │
        ▼                      │ ①metadata filter (DynamoDB scan)     │ start/poll   │ filtered vs
  ┌───────────┐                │ ②filtered retrieve (Bedrock KB)      │ KB ingestion │ unfiltered
  │    S3     │                │ ③answer (LLM)                        │ jobs         │ A/B run
  │ documents │                ▼                                      ▼              ▼
  └─────┬─────┘          ┌──────────────────── Amazon Bedrock Knowledge Base ────────────────┐
        │ ObjectCreated  │  vector store: Amazon S3 Vectors · embeddings: Titan Text v2 (1024d)│
        ▼                └───────────────────────────────────────────────────────────────────┘
  ┌──────────────────┐                                     ▲
  │ memory_optimizer │  start async Textract job           │ ingest chunks + sidecar metadata
  └────────┬─────────┘                                     │
           │ SNS (Textract done)                           │
           ▼                                               │
  ┌──────────────────┐   LLM metadata extraction    ┌──────┴──────┐
  │ result_processor │ ───────────────────────────▶ │  DynamoDB   │  documents table
  └──────────────────┘   {title,summary,topics,      │  (metadata  │  (+ eval_results table)
                          facts,document_type}        │   index)    │
                                                      └─────────────┘
```

### Ingestion flow (write path)

1. **Upload.** The browser calls `POST /uploads`; the **`upload_url`** Lambda returns a short-lived presigned S3 `PUT` URL. The browser uploads the PDF directly to S3 under `uploads/<uuid>/<filename>`.
2. **Kick off OCR.** The S3 `ObjectCreated` event triggers **`memory_optimizer`**, which writes an initial record to DynamoDB (`processing_status = RECEIVED`) and starts an **asynchronous Amazon Textract** text-detection job, wired to an SNS topic for completion.
3. **Duplicate check.** When Textract finishes, SNS invokes **`result_processor`**. It pulls the OCR text, stores it at `extracted-text/<document_id>.txt`, and computes a **SHA-256 fingerprint of the normalized text**. If another document already carries the same fingerprint, the new one is marked `DUPLICATE` / `SKIPPED_DUPLICATE` with a `duplicate_of` pointer and processing stops — **before** the LLM call, the sidecar write, and ingestion. Redundant copies cost no extraction tokens and can never pollute the vector store.
4. **Extract metadata.** For non-duplicates, an LLM extracts structured metadata: `{title, summary, topics[], facts[], document_type}`. `result_processor` writes:
   - the metadata + content fingerprint into DynamoDB (`metadata_status = COMPLETED`, `knowledge_base_status = READY_FOR_INGESTION`), and
   - a **`.metadata.json` sidecar** next to the document in S3, carrying `document_id`, `document_type`, and `title` as Bedrock KB `metadataAttributes`. This is what makes per-document filtering possible at retrieval time.
5. **Sync to the vector store.** **`kb_sync`** (`POST /sync`) starts a Bedrock Knowledge Base ingestion job over all `READY_FOR_INGESTION` documents; Bedrock chunks them, embeds each chunk with Titan Text v2, and writes vectors + metadata into **Amazon S3 Vectors**. `GET /sync` polls and refreshes job status, advancing documents to `SYNCED` once ingestion completes.

### Query flow (read path)

The **`query`** Lambda (`POST /query`) implements the three-step core idea:

1. **Metadata pre-filter (the router).** Tokenize the question (stop-words removed) and score each *synced* document with **IDF-weighted term matching** against its `title`/`summary`/`topics`/`facts`/`document_type`:

   `score(doc) = Σ_{t ∈ Q∩D} log(1 + N/df(t)) / sqrt(|D|)`

   Near-duplicate documents share most of their metadata terms; only a few discriminative terms (country, year, version) separate them. IDF weighting makes those rare terms dominate the score, so the router disambiguates duplicates by exactly the fields that distinguish them. Candidates scoring below **75% of the top score** are dropped (genuinely ambiguous questions score candidates evenly and keep them all); at most `MAX_DOCUMENT_CANDIDATES` survive. The DynamoDB scan is cached across warm invocations (`DOCUMENTS_CACHE_TTL_SECONDS`, default 45s).
2. **Filtered retrieval.** Call the Bedrock KB `retrieve` API with a metadata filter (`document_id ∈ candidates`), so vector search only sees chunks from the selected documents.
3. **Grounded answer.** Feed the retrieved chunks to the LLM with a strict "answer only from context" prompt, and return the answer with citations.

**Recall safety net:** if term matching finds *no* candidates (short/ambiguous question, or a document not yet `SYNCED`), the query falls back to **unfiltered** retrieval rather than returning nothing.

**Explainability:** every response reports `metadata_filter_applied`, the `selected_documents` (title, score, matched terms — rendered in the chat UI so you can see *why* the router picked each document), and a `usage_summary` including `unique_source_documents` — how many distinct documents the retrieved context mixes together, the label-free noise indicator.

---

## Components

### Lambda functions (`Lambda_src/`)

| Function | Trigger | Responsibility |
|----------|---------|----------------|
| `upload_url` | `POST /uploads` | Issue presigned S3 PUT URLs for direct browser upload. |
| `memory_optimizer` | S3 `ObjectCreated` | Record the document and start the async Textract OCR job. |
| `result_processor` | SNS (Textract done) | OCR → duplicate check (content hash) → LLM metadata extraction → DynamoDB + KB sidecar metadata. |
| `kb_sync` | `POST /sync`, `GET /sync` | Start Bedrock KB ingestion jobs and track their status. |
| `query` | `POST /query` | Metadata-filtered retrieval + grounded answer (the core pipeline). |
| `eval` | `POST/GET/DELETE /eval` | A/B benchmark harness: filtered vs. unfiltered retrieval. |

### Data stores

- **DynamoDB `documents`** (`document_id` hash key) — the metadata index: extracted fields plus the `processing_status` / `metadata_status` / `knowledge_base_status` state machine and Textract/ingestion job IDs. Pay-per-request, encrypted at rest. Fine as a scan-based index at prototype scale (~thousands of docs); it is deliberately *not* a search engine.
- **DynamoDB `eval_results`** (`eval_id` hash key) — stored benchmark runs for the eval dashboard.
- **S3 `documents` bucket** — original PDFs (`uploads/`), OCR text (`extracted-text/`), and KB metadata sidecars.
- **Amazon S3 Vectors** — the Bedrock Knowledge Base vector store (`euclidean` distance, 1024-dim Titan Text v2 embeddings).

### Managed services

- **Amazon Textract** — asynchronous OCR / text detection for PDFs.
- **Amazon Bedrock Knowledge Base** — chunking, embedding, and filtered vector retrieval over S3 Vectors.
- **LLM (OpenAI-compatible endpoint)** — used for both metadata extraction and answer generation. Configurable via `deepseek_api_url` / `deepseek_model`; the API key lives in **AWS Secrets Manager**. (The `deepseek` naming is legacy — any OpenAI-compatible chat model works.)
- **API Gateway (HTTP API)** — single API fronting all Lambdas, with a 29s integration cap that shapes the query/eval timeouts.

### Frontend (`Frontend/`)

A static site hosted from S3 — no build step. Three pages backed by `config.js`/`app.js`/`styles.css`:

- **`index.html`** — upload documents and watch them move through the processing states.
- **`chat.html`** — ask questions; shows the answer, citations, and whether the metadata filter was applied.
- **`eval.html`** — the evaluation dashboard (see below).

### Why these choices (prototype economics)

| Concern | Choice | Why |
|---------|--------|-----|
| Vector store | **Amazon S3 Vectors** | A Bedrock KB on OpenSearch Serverless costs roughly **$350/month even when idle**; S3 Vectors is a few dollars/month. Decisive for a student budget. (`opensearch_serverless.tf` is a vestige of the earlier setup.) |
| Metadata index | **DynamoDB scan + term overlap** | Zero-cost-at-idle, dead simple, and sufficient to *demonstrate and measure* the filtering effect at this scale. A production system would use a real search/index engine here. |
| OCR | **Textract async + SNS** | Fully managed, event-driven, no servers to run. |
| LLM | **External OpenAI-compatible API** | Cheaper/simpler than self-hosting; swappable via one Terraform variable. |

---

## Measuring the effect (the eval harness)

The whole point is to show that filtering *helps*, so measurement is built in. The **`eval`** Lambda (`POST /eval`) runs the **same question through both pipelines** — filtered and unfiltered — concurrently, and scores them on three levels:

**1. Retrieval noise (needs a labeled expected document — by `expected_document_id` or, more conveniently, `expected_document_filename`, resolved against DynamoDB so test sets survive re-uploads):**

- `context_precision` — fraction of retrieved chunks that come from the expected document. `1 − precision` is the share of **wrong-document noise** reaching the LLM: the headline metric for the noise problem.
- `distractor_chunks` — the noise, counted directly.
- `first_relevant_rank` / `reciprocal_rank` (→ **MRR** in the summary) — does the right document rank *first*, not just "somewhere in top-k"?
- `correct_document_hit` — document-level recall, as before.

**2. Answer quality (needs an `expected_answer` reference):** a **blind LLM judge** grades both answers against the reference in one call — answers are presented as anonymous "Answer A/B" in random order, so the judge can't favor a pipeline by name or position. Verdicts: `correct` / `partially_correct` / `incorrect` (+ a one-line reason). This is the end-to-end proof that filtering produces *more correct answers*, not just cleaner context. Judging is best-effort: if the judge call fails, the run still records all retrieval metrics.

**3. Cost & speed (always recorded, no labels needed):** `estimated_context_tokens` + `context_token_reduction_percent` (filtered vs. unfiltered, actually measured), `unique_source_documents` (how many documents the context mixes together), and `retrieval_ms` / `answer_ms` / `total_ms` latency per pipeline.

`GET /eval` returns all stored runs plus an aggregate summary: hit rate, **avg context precision**, **MRR**, **answer accuracy** (correct = 1, partial = 0.5), avg unique source docs, avg token reduction, and avg latency — each as *unfiltered vs. filtered*. `DELETE /eval` clears the history.

### The eval dashboard & batch runs

`eval.html` runs single labeled questions side-by-side, or a whole **test set in one click**: upload a `testset.json` (array of `{question, expected_document_filename, expected_answer}`) and it executes every case sequentially, then shows the aggregate summary. Results export as CSV.

### Reproducible demo corpus

`scripts/generate_test_corpus.py` (zero dependencies) generates a controlled "noisy enterprise corpus" that reproduces the exact failure modes Aethon targets, plus a labeled 10-question `testset.json`:

- **Near-duplicates / ambiguity** — the same data-retention policy for Germany, France, and Spain: identical boilerplate, different key facts (retention periods, authorities, fines, deadlines).
- **Exact duplicate** — a byte-identical copy of the German policy under another filename, which the ingestion pipeline should flag as `SKIPPED_DUPLICATE` without spending a single LLM token.
- **Versioned redundancy** — 2023 vs. 2026 editions of an employee handbook that disagree on vacation days, remote work, and probation.

```bash
python3 scripts/generate_test_corpus.py --out ./corpus
# upload the PDFs (the *_copy.pdf last, after the original completes), sync,
# then run corpus/testset.json from the eval page's batch runner
```

---

## Repository layout

```text
Aethon/
├── Lambda_src/
│   ├── upload_url/          # Presigned upload URLs
│   ├── memory_optimizer/    # S3 event → Textract job
│   ├── result_processor/    # Textract → dedup check → LLM metadata → DynamoDB + KB sidecar
│   ├── kb_sync/             # Bedrock KB ingestion orchestration
│   ├── query/               # Metadata-filtered RAG query pipeline (IDF router)
│   └── eval/                # Filtered vs. unfiltered benchmark harness + LLM judge
├── Infrastructure/          # Main Terraform stack (remote S3 state)
├── Frontend/                # Static S3-hosted UI (upload / chat / eval)
├── scripts/                 # Demo corpus + labeled test-set generator
├── bootstrap/               # One-time OIDC + remote-state setup (local state)
├── IMPROVEMENTS.md          # Improvement plan & step-by-step status
└── .github/workflows/       # GitHub Actions deployment pipeline
```

---

## Deployment

Infrastructure is deployed with **Terraform**. GitHub Actions applies changes automatically on pushes to `main` using **OIDC** — no long-lived AWS keys are stored in GitHub. Default region: **`eu-central-1`**.

### Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/downloads) `>= 1.6.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) v2
- An AWS account with permission to create IAM, S3, DynamoDB, Lambda, API Gateway, Textract, SNS, Secrets Manager, and Bedrock resources
- **Amazon Bedrock model access** enabled for the Titan Text Embeddings V2 model in your region
- An API key for your chosen OpenAI-compatible LLM endpoint

### 1. Local AWS credentials (one-time)

```bash
aws configure --profile aethon
export AWS_PROFILE=aethon
export AWS_REGION=eu-central-1
aws sts get-caller-identity   # verify
```

### 2. Bootstrap the CI/CD foundation (one-time, local state)

Creates the Terraform remote-state bucket, the state-lock table, and the GitHub Actions OIDC provider + deploy role.

```bash
cd bootstrap
cp terraform.tfvars.example terraform.tfvars   # edit if needed
terraform init
terraform apply
```

Re-run bootstrap whenever `bootstrap/` changes. The GitHub Actions workflow only applies `Infrastructure/`, so **new deploy-role permissions** (e.g. additional Bedrock, SNS, or Secrets Manager actions) must be applied locally here before the pipeline can create those resources.

Save the outputs:

```bash
terraform output github_actions_role_arn
terraform output terraform_state_bucket
terraform output terraform_lock_table
terraform output -raw backend_config
```

### 3. Configure GitHub (Settings → Secrets and variables → Actions)

**Secret:** `AWS_ROLE_ARN` = the `github_actions_role_arn` output.

**Variables:** `TF_STATE_BUCKET` and `TF_LOCK_TABLE` from the outputs. Optional: `AWS_REGION` (default `eu-central-1`), `TF_VAR_project_name` (`aethon`), `TF_VAR_environment` (`dev`).

### 4. Store the LLM API key

After the main stack first applies (which creates the Secrets Manager secret), put your LLM key in it:

```bash
aws secretsmanager put-secret-value \
  --secret-id aethon/dev/deepseek-api-key \
  --secret-string 'YOUR_LLM_API_KEY'
```

### 5. Deploy the main stack

Push to `main` (or run the workflow manually) to have GitHub Actions `fmt` → `validate` → `plan` → `apply` the `Infrastructure/` stack. Pull requests get a `plan` posted as a comment; they are not applied.

To run locally instead:

```bash
cd Infrastructure
cp backend.hcl.example backend.hcl   # fill in bootstrap output values
terraform init -backend-config=backend.hcl
terraform apply
```

### 6. Configurable Terraform variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `aws_region` | `eu-central-1` | Deployment region. |
| `allowed_cors_origins` | `["http://localhost:3000"]` | Frontend origins allowed to call the API. |
| `deepseek_api_url` | `https://api.cortecs.ai/v1/chat/completions` | OpenAI-compatible chat endpoint. |
| `deepseek_model` | `deepseek-v3.2` | Chat model for metadata extraction & answers. |
| `embedding_model_id` | `amazon.titan-embed-text-v2:0` | Bedrock embedding model. |
| `knowledge_base_chunk_max_tokens` | `500` | Fixed-size chunk size. |
| `knowledge_base_chunk_overlap_percentage` | `20` | Chunk overlap. |

---

## Using it

After deploy, read the endpoints from Terraform outputs (`terraform output` in `Infrastructure/`), then:

```bash
# 1) Get a presigned upload URL and upload a PDF
curl -s -X POST "$API/uploads" \
  -H "Content-Type: application/json" \
  -d '{"filename":"example.pdf","content_type":"application/pdf"}'
curl -X PUT "$UPLOAD_URL" -H "Content-Type: application/pdf" --upload-file example.pdf

# 2) After processing, sync ready documents into the Knowledge Base
curl -X POST "$API/sync"
curl "$API/sync"          # poll until documents reach SYNCED

# 3) Ask a question (metadata-filtered RAG)
curl -s -X POST "$API/query" \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the data retention rules?"}'

# 4) Benchmark filtered vs. unfiltered retrieval (labels unlock precision,
#    MRR and the blind LLM judge; the filename form survives re-uploads)
curl -s -X POST "$API/eval" \
  -H "Content-Type: application/json" \
  -d '{
        "question": "How long must customer records be retained in Germany?",
        "expected_document_filename": "germany_data_retention_policy.pdf",
        "expected_answer": "Customer records must be retained for 10 years."
      }'
curl "$API/eval"          # all runs + aggregate summary
```

Or just use the hosted frontend: **upload** on `index.html`, **ask** on `chat.html`, **benchmark** on `eval.html` (single runs or a whole `testset.json` via the batch runner).

---

## Security notes (prototype)

This is a research prototype and is **not hardened for production**:

- The API and the frontend bucket are **public / unauthenticated**. Add Cognito or a JWT authorizer and lock down CORS before any real use.
- GitHub Actions uses short-lived OIDC credentials — do **not** add static `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` to the repo.
- Bootstrap state stays local; the main stack's state lives in the remote S3 backend.
- The LLM API key is held in Secrets Manager, not in code or Terraform variables.
```