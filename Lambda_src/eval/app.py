import json
import logging
import math
import os
import random
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
secrets_client = boto3.client("secretsmanager")
dynamodb = boto3.resource("dynamodb")

DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
EVAL_RESULTS_TABLE = os.environ["EVAL_RESULTS_TABLE"]
DEEPSEEK_API_KEY_SECRET_ARN = os.environ["DEEPSEEK_API_KEY_SECRET_ARN"]
DEEPSEEK_API_URL = os.environ["DEEPSEEK_API_URL"]
DEEPSEEK_MODEL = os.environ["DEEPSEEK_MODEL"]
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
MAX_RETRIEVAL_RESULTS = int(os.environ.get("MAX_RETRIEVAL_RESULTS", "5"))
MAX_DOCUMENT_CANDIDATES = int(os.environ.get("MAX_DOCUMENT_CANDIDATES", "3"))
# Candidates scoring below this fraction of the top score are dropped instead
# of padding the filter out to top-k with weak matches. Near-duplicates that
# miss the question's discriminative terms (country, year, version) land
# well under this margin; genuinely ambiguous questions score candidates
# evenly and keep them all.
RELATIVE_SCORE_THRESHOLD = float(os.environ.get("RELATIVE_SCORE_THRESHOLD", "0.65"))
SYNCED_KNOWLEDGE_BASE_STATUSES = {"SYNCED", "COMPLETE", "COMPLETED"}
# Both answer pipelines run concurrently, then one judge call runs after them.
# Worst case must stay inside the 28s Lambda / 29s API Gateway budget:
# retrieval (~2s) + answer (15s) + judge (8s) < 28s.
LLM_ANSWER_TIMEOUT_SECONDS = int(os.environ.get("LLM_ANSWER_TIMEOUT_SECONDS", "15"))
LLM_JUDGE_TIMEOUT_SECONDS = int(os.environ.get("LLM_JUDGE_TIMEOUT_SECONDS", "8"))
JUDGE_VERDICTS = {"correct", "partially_correct", "incorrect"}

_deepseek_api_key = None
table = dynamodb.Table(DOCUMENTS_TABLE)
eval_table = dynamodb.Table(EVAL_RESULTS_TABLE)


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _get_deepseek_api_key():
    global _deepseek_api_key

    if _deepseek_api_key:
        return _deepseek_api_key

    response = secrets_client.get_secret_value(SecretId=DEEPSEEK_API_KEY_SECRET_ARN)
    _deepseek_api_key = response["SecretString"].strip()

    return _deepseek_api_key


def _deepseek_request(payload, timeout_seconds):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {_get_deepseek_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "Aethon/1.0 (AWS Lambda evaluation)",
    }

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            DEEPSEEK_API_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            # The provider's auth backend intermittently returns a 503 when
            # eval's filtered/unfiltered pipelines hit it concurrently with
            # the same API key, and says to retry — back off briefly rather
            # than failing the whole request.
            if error.code in (500, 502, 503, 504) and attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"DeepSeek API request failed: {error.code} {error_body}") from error
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            raise RuntimeError("The LLM API request timed out. Please try again.") from error


def _extract_json_object(content):
    content = content.strip()

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced_match:
        content = fenced_match.group(1)
    elif not content.startswith("{"):
        # Some models prose-wrap the JSON even when asked not to; fall back
        # to the outermost {...} span rather than failing the whole request.
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            content = brace_match.group(0)

    return json.loads(content)


# Document metadata is extracted from structured facts and uses the country
# noun ("Germany"); questions are often phrased with the demonym adjective
# ("German"). Plain token overlap treats these as unrelated words, which was
# silently tanking the score of the correct document in country-labeled
# near-duplicate clusters. Expanding the question side to include the noun
# form (and its constituent words, for multi-word countries) fixes this
# without needing to touch how document metadata is stored.
_COUNTRY_DEMONYMS = {
    "german": ("germany",),
    "french": ("france",),
    "spanish": ("spain",),
    "japanese": ("japan",),
    "dutch": ("netherlands",),
    "canadian": ("canada",),
    "australian": ("australia",),
    "singaporean": ("singapore",),
    "american": ("united", "states"),
    "british": ("united", "kingdom"),
}


def _question_terms(question):
    stop_words = {
        "a", "an", "and", "are", "about", "can", "does", "for", "from", "how",
        "in", "is", "it", "of", "on", "that", "the", "this", "to", "what", "which",
        "with",
    }
    terms = {
        term
        for term in re.findall(r"[a-z0-9]+", question.lower())
        if len(term) > 2 and term not in stop_words
    }
    for term in list(terms):
        terms.update(_COUNTRY_DEMONYMS.get(term, ()))
    return terms


def _document_terms(item):
    values = [
        item.get("title", ""),
        item.get("summary", ""),
        item.get("document_type", ""),
        *item.get("topics", []),
        *item.get("facts", []),
    ]
    return {
        term
        for value in values
        for term in re.findall(r"[a-z0-9]+", str(value).lower())
        if len(term) > 2
    }


def _scan_documents_table():
    items = []
    response = table.scan()

    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = table.scan(ExclusiveStartKey=last_key)

    return items


def _select_candidate_documents(question, items):
    """Rank synced documents against the question with IDF-weighted term matching.

    Near-duplicate documents share most of their metadata terms; only a few
    discriminative terms (country, year, version) separate them. Weighting
    matched terms by inverse document frequency makes those rare terms
    dominate the score, so the router disambiguates duplicates by exactly the
    fields that distinguish them. Length normalization stops long-metadata
    documents from always winning; a relative threshold drops weak candidates
    instead of padding the filter out to top-k.

    NOTE: intentionally duplicated in Lambda_src/query/app.py — keep in sync.
    """
    question_terms = _question_terms(question)
    if not question_terms:
        return []

    eligible = []
    for item in items:
        if item.get("metadata_status") != "COMPLETED":
            continue
        if item.get("knowledge_base_status") not in SYNCED_KNOWLEDGE_BASE_STATUSES:
            continue
        if not item.get("document_id"):
            continue
        eligible.append((item, _document_terms(item)))

    if not eligible:
        return []

    document_frequency = Counter(
        term for _, terms in eligible for term in terms
    )
    total_documents = len(eligible)

    scored = []
    for item, terms in eligible:
        matched = question_terms.intersection(terms)
        if not matched:
            continue

        weight = sum(
            math.log(1 + total_documents / document_frequency[term])
            for term in matched
        )
        score = weight / math.sqrt(len(terms) or 1)
        scored.append(
            {
                "document_id": item["document_id"],
                "title": str(item.get("title", "")),
                "score": round(score, 4),
                "matched_terms": sorted(matched)[:8],
            }
        )

    scored.sort(key=lambda candidate: candidate["score"], reverse=True)
    if not scored:
        return []

    top_score = scored[0]["score"]
    qualified = [
        candidate
        for candidate in scored
        if candidate["score"] >= top_score * RELATIVE_SCORE_THRESHOLD
    ]
    return qualified[:MAX_DOCUMENT_CANDIDATES]


def _resolve_expected_document(body, items):
    """Resolve the expected document from an explicit ID or a filename.

    Filenames make labeled test sets portable across re-uploads (document IDs
    change every upload). Prefer synced documents and the most recent record
    when several share a filename.
    """
    expected_document_id = str(body.get("expected_document_id", "")).strip() or None
    expected_filename = str(body.get("expected_document_filename", "")).strip() or None

    if expected_document_id or not expected_filename:
        return expected_document_id, expected_filename

    matches = [
        item
        for item in items
        if str(item.get("filename", "")).lower() == expected_filename.lower()
        and item.get("document_id")
    ]
    synced = [
        item
        for item in matches
        if item.get("knowledge_base_status") in SYNCED_KNOWLEDGE_BASE_STATUSES
    ]
    pool = synced or matches
    if pool:
        best = max(pool, key=lambda item: str(item.get("created_at", "")))
        expected_document_id = best["document_id"]

    return expected_document_id, expected_filename


def _document_id_filter(document_ids):
    filters = [
        {"equals": {"key": "document_id", "value": document_id}}
        for document_id in document_ids
    ]
    return filters[0] if len(filters) == 1 else {"orAll": filters}


def _document_id_from_source(uri):
    if not uri:
        return None

    # s3://bucket/uploads/<document_id>/<filename>
    parts = uri.split("://", 1)[-1].split("/")
    if len(parts) >= 3 and parts[1] == "uploads":
        return parts[2]

    return None


def _retrieve_context(question, document_ids=None):
    vector_config = {"numberOfResults": MAX_RETRIEVAL_RESULTS}
    if document_ids:
        vector_config["filter"] = _document_id_filter(document_ids)

    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": vector_config},
    )

    contexts = []

    for index, result in enumerate(response.get("retrievalResults", []), start=1):
        text = result.get("content", {}).get("text", "").strip()
        if not text:
            continue

        location = result.get("location", {})
        s3_uri = location.get("s3Location", {}).get("uri")
        score = result.get("score")

        contexts.append(
            {
                "id": index,
                "text": text,
                "source": s3_uri,
                "score": score,
            }
        )

    return contexts


def _estimate_tokens(text):
    return (len(text) + 3) // 4


def _answer_with_deepseek(question, contexts):
    context_text = "\n\n".join(
        f"[Source {context['id']}]\n{context['text']}" for context in contexts
    )

    system_prompt = """
You are Aethon's document question-answering assistant.
Answer using only the supplied document context.
If the context is insufficient, say what is missing instead of inventing details.
Keep the answer clear, concise, and useful.
Mention source numbers when they support important claims.
""".strip()

    user_prompt = f"""
Question:
{question}

Document context:
{context_text or "No relevant document context was found."}
""".strip()

    response = _deepseek_request(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 1800,
        },
        LLM_ANSWER_TIMEOUT_SECONDS,
    )

    return response["choices"][0]["message"]["content"].strip()


def _run_pipeline(question, document_ids):
    retrieval_started = time.monotonic()
    contexts = _retrieve_context(question, document_ids)
    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)

    answer_started = time.monotonic()
    answer = (
        _answer_with_deepseek(question, contexts)
        if contexts
        else "I don't know based on the uploaded documents."
    )
    answer_ms = int((time.monotonic() - answer_started) * 1000)

    retrieved_document_ids = sorted(
        {
            document_id
            for context in contexts
            if (document_id := _document_id_from_source(context["source"]))
        }
    )

    return {
        "answer": answer,
        "retrieved_chunks": len(contexts),
        "estimated_context_tokens": sum(_estimate_tokens(c["text"]) for c in contexts),
        "retrieved_document_ids": retrieved_document_ids,
        "unique_source_documents": len(retrieved_document_ids),
        "retrieval_ms": retrieval_ms,
        "answer_ms": answer_ms,
        "total_ms": retrieval_ms + answer_ms,
        "citations": [
            {
                "id": context["id"],
                "source": context["source"],
                "score": context["score"],
                "preview": context["text"][:400],
            }
            for context in contexts
        ],
    }


def _apply_labeled_metrics(result, expected_document_id):
    """Chunk-level retrieval quality against the labeled expected document.

    - correct_document_hit: expected document appears anywhere in the results
    - context_precision:    fraction of retrieved chunks from the expected doc
                            (1 - precision is the wrong-document noise share)
    - distractor_chunks:    chunks retrieved from other documents
    - first_relevant_rank / reciprocal_rank: does the right doc rank first?
    """
    result["correct_document_hit"] = None
    result["context_precision"] = None
    result["distractor_chunks"] = None
    result["first_relevant_rank"] = None
    result["reciprocal_rank"] = None

    if not expected_document_id:
        return

    ordered_ids = [
        _document_id_from_source(citation["source"]) for citation in result["citations"]
    ]
    hits = [document_id == expected_document_id for document_id in ordered_ids]

    result["correct_document_hit"] = any(hits)
    result["reciprocal_rank"] = 0.0

    if not ordered_ids:
        result["distractor_chunks"] = 0
        return

    relevant = sum(hits)
    result["context_precision"] = round(relevant / len(ordered_ids), 3)
    result["distractor_chunks"] = len(ordered_ids) - relevant

    first_rank = next((rank for rank, hit in enumerate(hits, start=1) if hit), None)
    if first_rank:
        result["first_relevant_rank"] = first_rank
        result["reciprocal_rank"] = round(1.0 / first_rank, 3)


def _judge_answers(question, expected_answer, filtered_answer, unfiltered_answer):
    """Grade both pipeline answers against the reference with one blind LLM call.

    The judge sees anonymous "Answer A"/"Answer B" in random order, so it
    cannot favor a pipeline by name or position.
    """
    order_swapped = random.random() < 0.5
    answer_a, answer_b = (
        (unfiltered_answer, filtered_answer)
        if order_swapped
        else (filtered_answer, unfiltered_answer)
    )

    system_prompt = """
You are an impartial grader for a question-answering benchmark.
Compare each candidate answer against the reference answer for factual agreement.
Judge only whether the candidate conveys the same facts as the reference:
"correct" (agrees on the key facts), "partially_correct" (some key facts right,
some missing or wrong), or "incorrect" (contradicts or misses the key facts,
or refuses to answer).
Return only valid json, with no markdown code fences and no commentary.
""".strip()

    user_prompt = f"""
Question:
{question}

Reference answer:
{expected_answer}

Answer A:
{answer_a}

Answer B:
{answer_b}

Return json with this exact structure:
{{
  "answer_a": {{"verdict": "correct|partially_correct|incorrect", "reason": "one short sentence"}},
  "answer_b": {{"verdict": "correct|partially_correct|incorrect", "reason": "one short sentence"}}
}}
""".strip()

    response = _deepseek_request(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 400,
        },
        LLM_JUDGE_TIMEOUT_SECONDS,
    )

    parsed = _extract_json_object(response["choices"][0]["message"]["content"])

    def _clean(entry):
        verdict = str(entry.get("verdict", "")).strip().lower().replace(" ", "_")
        if verdict not in JUDGE_VERDICTS:
            raise ValueError(f"Unexpected judge verdict: {verdict!r}")
        return {"verdict": verdict, "reason": str(entry.get("reason", "")).strip()[:500]}

    graded_a = _clean(parsed.get("answer_a") or {})
    graded_b = _clean(parsed.get("answer_b") or {})
    filtered_grade, unfiltered_grade = (
        (graded_b, graded_a) if order_swapped else (graded_a, graded_b)
    )

    return {
        "filtered": filtered_grade,
        "unfiltered": unfiltered_grade,
        "order_swapped": order_swapped,
    }


def _reduction_percent(unfiltered_tokens, filtered_tokens):
    if not unfiltered_tokens:
        return 0
    return round(max(0, 1 - (filtered_tokens / unfiltered_tokens)) * 100)


def _handle_run(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"message": "Request body must be valid JSON."})

    question = str(body.get("question", "")).strip()
    if not question:
        return _response(400, {"message": "question is required."})

    expected_answer = str(body.get("expected_answer", "")).strip() or None

    items = _scan_documents_table()
    candidate_documents = _select_candidate_documents(question, items)
    candidate_document_ids = [
        candidate["document_id"] for candidate in candidate_documents
    ]
    expected_document_id, expected_document_filename = _resolve_expected_document(
        body, items
    )

    # Run both pipelines concurrently so the comparison's total latency is
    # close to a single query's, keeping it inside API Gateway's ~29s cap.
    with ThreadPoolExecutor(max_workers=2) as executor:
        filtered_future = executor.submit(_run_pipeline, question, candidate_document_ids or None)
        unfiltered_future = executor.submit(_run_pipeline, question, None)
        filtered = filtered_future.result()
        unfiltered = unfiltered_future.result()

    filtered["documents_selected"] = len(candidate_document_ids)
    filtered["metadata_filter_applied"] = bool(candidate_document_ids)
    _apply_labeled_metrics(filtered, expected_document_id)
    _apply_labeled_metrics(unfiltered, expected_document_id)

    judge = None
    if expected_answer:
        try:
            judge = _judge_answers(
                question, expected_answer, filtered["answer"], unfiltered["answer"]
            )
        except Exception as error:
            # Judging is best-effort: a judge failure must not lose the
            # retrieval metrics of an otherwise successful comparison.
            logger.exception("LLM judge failed")
            judge = {"error": str(error)}

    if judge and not judge.get("error"):
        filtered["answer_verdict"] = judge["filtered"]["verdict"]
        filtered["answer_verdict_reason"] = judge["filtered"]["reason"]
        unfiltered["answer_verdict"] = judge["unfiltered"]["verdict"]
        unfiltered["answer_verdict_reason"] = judge["unfiltered"]["reason"]

    record = {
        "eval_id": str(uuid.uuid4()),
        "question": question,
        "expected_document_id": expected_document_id,
        "expected_document_filename": expected_document_filename,
        "expected_answer": expected_answer,
        "candidate_documents": candidate_documents,
        "filtered": filtered,
        "unfiltered": unfiltered,
        "context_token_reduction_percent": _reduction_percent(
            unfiltered["estimated_context_tokens"], filtered["estimated_context_tokens"]
        ),
        "judge": judge,
        "created_at": _utc_now(),
    }

    eval_table.put_item(Item=json.loads(json.dumps(record), parse_float=Decimal))

    return _response(200, record)


def _summary(items):
    count = len(items)
    if count == 0:
        return {"count": 0}

    def pipeline_values(mode, key):
        values = []
        for item in items:
            pipeline = item.get(mode)
            if isinstance(pipeline, dict) and pipeline.get(key) is not None:
                values.append(float(pipeline[key]))
        return values

    def avg(values, digits=1):
        if not values:
            return None
        value = round(sum(values) / len(values), digits)
        return int(value) if digits == 0 else value

    def hit_rate(mode):
        hits = [
            item[mode]["correct_document_hit"]
            for item in items
            if item.get(mode, {}).get("correct_document_hit") is not None
        ]
        if not hits:
            return None
        return round(100 * sum(1 for hit in hits if hit) / len(hits))

    def answer_accuracy(mode):
        # correct = 1, partially_correct = 0.5, incorrect = 0
        verdicts = []
        for item in items:
            pipeline = item.get(mode)
            if isinstance(pipeline, dict) and pipeline.get("answer_verdict"):
                verdicts.append(pipeline["answer_verdict"])
        if not verdicts:
            return None
        score = sum(
            1.0 if verdict == "correct" else 0.5 if verdict == "partially_correct" else 0.0
            for verdict in verdicts
        )
        return round(100 * score / len(verdicts))

    reduction_values = [
        float(item["context_token_reduction_percent"])
        for item in items
        if item.get("context_token_reduction_percent") is not None
    ]

    return {
        "count": count,
        "labeled_count": sum(1 for item in items if item.get("expected_document_id")),
        "judged_count": sum(
            1
            for item in items
            if isinstance(item.get("filtered"), dict)
            and item["filtered"].get("answer_verdict")
        ),
        "avg_context_token_reduction_percent": avg(reduction_values) or 0,
        "avg_filtered_chunks": avg(pipeline_values("filtered", "retrieved_chunks")),
        "avg_unfiltered_chunks": avg(pipeline_values("unfiltered", "retrieved_chunks")),
        "avg_filtered_context_tokens": avg(
            pipeline_values("filtered", "estimated_context_tokens")
        ),
        "avg_unfiltered_context_tokens": avg(
            pipeline_values("unfiltered", "estimated_context_tokens")
        ),
        "filtered_hit_rate_percent": hit_rate("filtered"),
        "unfiltered_hit_rate_percent": hit_rate("unfiltered"),
        "avg_filtered_context_precision": avg(
            pipeline_values("filtered", "context_precision"), digits=2
        ),
        "avg_unfiltered_context_precision": avg(
            pipeline_values("unfiltered", "context_precision"), digits=2
        ),
        "filtered_mrr": avg(pipeline_values("filtered", "reciprocal_rank"), digits=2),
        "unfiltered_mrr": avg(pipeline_values("unfiltered", "reciprocal_rank"), digits=2),
        "avg_filtered_unique_documents": avg(
            pipeline_values("filtered", "unique_source_documents")
        ),
        "avg_unfiltered_unique_documents": avg(
            pipeline_values("unfiltered", "unique_source_documents")
        ),
        "filtered_answer_accuracy_percent": answer_accuracy("filtered"),
        "unfiltered_answer_accuracy_percent": answer_accuracy("unfiltered"),
        "avg_filtered_total_ms": avg(pipeline_values("filtered", "total_ms"), digits=0),
        "avg_unfiltered_total_ms": avg(pipeline_values("unfiltered", "total_ms"), digits=0),
    }


def _handle_list():
    items = []
    response = eval_table.scan()

    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = eval_table.scan(ExclusiveStartKey=last_key)

    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)

    return _response(200, {"results": items, "summary": _summary(items)})


def _handle_clear():
    deleted = 0
    response = eval_table.scan(ProjectionExpression="eval_id")

    with eval_table.batch_writer() as batch:
        while True:
            for item in response.get("Items", []):
                batch.delete_item(Key={"eval_id": item["eval_id"]})
                deleted += 1

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            response = eval_table.scan(
                ProjectionExpression="eval_id", ExclusiveStartKey=last_key
            )

    return _response(200, {"deleted_count": deleted})


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    try:
        if method == "POST":
            return _handle_run(event)
        if method == "GET":
            return _handle_list()
        if method == "DELETE":
            return _handle_clear()
        return _response(405, {"message": "Method not allowed."})
    except Exception as error:
        logger.exception("Eval request failed")
        return _response(500, {"message": str(error)})
