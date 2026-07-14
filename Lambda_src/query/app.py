import json
import logging
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections import Counter

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
secrets_client = boto3.client("secretsmanager")
dynamodb = boto3.resource("dynamodb")

DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
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
RELATIVE_SCORE_THRESHOLD = float(os.environ.get("RELATIVE_SCORE_THRESHOLD", "0.75"))
# Warm-invocation cache for the DynamoDB metadata index: the document set
# changes on upload/sync, not per query, so re-scanning on every question
# only adds latency and read cost.
DOCUMENTS_CACHE_TTL_SECONDS = int(os.environ.get("DOCUMENTS_CACHE_TTL_SECONDS", "45"))
SYNCED_KNOWLEDGE_BASE_STATUSES = {"SYNCED", "COMPLETE", "COMPLETED"}
LLM_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "12"))
_deepseek_api_key = None
_documents_cache = {"items": None, "expires_at": 0.0}
table = dynamodb.Table(DOCUMENTS_TABLE)


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _get_deepseek_api_key():
    global _deepseek_api_key

    if _deepseek_api_key:
        return _deepseek_api_key

    response = secrets_client.get_secret_value(SecretId=DEEPSEEK_API_KEY_SECRET_ARN)
    _deepseek_api_key = response["SecretString"].strip()

    return _deepseek_api_key


def _deepseek_request(payload):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {_get_deepseek_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": "Aethon/1.0 (AWS Lambda document query)",
    }

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            DEEPSEEK_API_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=LLM_REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            # The provider's auth backend intermittently returns a 503 under
            # concurrent requests sharing an API key and says to retry —
            # back off briefly rather than failing the whole request.
            if error.code in (500, 502, 503, 504) and attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"DeepSeek API request failed: {error.code} {error_body}") from error
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            raise RuntimeError("The LLM API request timed out. Please try again.") from error


def _question_terms(question):
    stop_words = {
        "a", "an", "and", "are", "about", "can", "does", "for", "from", "how",
        "in", "is", "it", "of", "on", "that", "the", "this", "to", "what", "which",
        "with",
    }
    return {
        term
        for term in re.findall(r"[a-z0-9]+", question.lower())
        if len(term) > 2 and term not in stop_words
    }


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


def _load_documents():
    now = time.monotonic()
    if _documents_cache["items"] is not None and now < _documents_cache["expires_at"]:
        return _documents_cache["items"]

    items = []
    response = table.scan()

    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = table.scan(ExclusiveStartKey=last_key)

    _documents_cache["items"] = items
    _documents_cache["expires_at"] = now + DOCUMENTS_CACHE_TTL_SECONDS
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

    NOTE: intentionally duplicated in Lambda_src/eval/app.py — keep in sync.
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


def _usage_summary(candidate_documents, contexts):
    unique_source_documents = len(
        {
            document_id
            for context in contexts
            if (document_id := _document_id_from_source(context["source"]))
        }
    )

    return {
        "documents_selected": len(candidate_documents),
        "retrieved_chunks": len(contexts),
        "estimated_context_tokens": sum(
            _estimate_tokens(context["text"]) for context in contexts
        ),
        "unique_source_documents": unique_source_documents,
    }


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
            "max_tokens": 900,
        }
    )

    return response["choices"][0]["message"]["content"].strip()


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"message": "Request body must be valid JSON."})

    question = str(body.get("question", "")).strip()

    if not question:
        return _response(400, {"message": "question is required."})

    try:
        items = _load_documents()
        candidate_documents = _select_candidate_documents(question, items)
        candidate_document_ids = [
            candidate["document_id"] for candidate in candidate_documents
        ]

        # Fall back to unfiltered retrieval when no DynamoDB candidates match —
        # prevents recall collapse on ambiguous/short queries, or when a
        # document's knowledge_base_status hasn't caught up to SYNCED yet.
        contexts = _retrieve_context(question, candidate_document_ids or None)

        if not contexts:
            return _response(
                200,
                {
                    "answer": "I don't know based on the uploaded documents.",
                    "citations": [],
                    "retrieved_count": 0,
                    "metadata_filter_applied": bool(candidate_document_ids),
                    "selected_documents": candidate_documents,
                    "usage_summary": _usage_summary(candidate_documents, []),
                },
            )
        answer = _answer_with_deepseek(question, contexts)
    except Exception as error:
        logger.exception("Query failed")
        return _response(500, {"message": str(error)})

    return _response(
        200,
        {
            "answer": answer,
            "citations": [
                {
                    "id": context["id"],
                    "source": context["source"],
                    "score": context["score"],
                    "preview": context["text"][:400],
                }
                for context in contexts
            ],
            "retrieved_count": len(contexts),
            "metadata_filter_applied": bool(candidate_document_ids),
            "selected_documents": candidate_documents,
            "usage_summary": _usage_summary(candidate_documents, contexts),
        },
    )
