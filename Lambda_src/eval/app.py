import json
import logging
import os
import re
import urllib.error
import urllib.request
import uuid
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


def _deepseek_request(payload):
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_get_deepseek_api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "Aethon/1.0 (AWS Lambda evaluation)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API request failed: {error.code} {error_body}") from error


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


def _select_candidate_document_ids(question):
    question_terms = _question_terms(question)
    if not question_terms:
        return []

    items = []
    response = table.scan()

    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = table.scan(ExclusiveStartKey=last_key)

    ranked_documents = []
    for item in items:
        if item.get("metadata_status") != "COMPLETED":
            continue

        document_id = item.get("document_id")
        if not document_id:
            continue

        score = len(question_terms.intersection(_document_terms(item)))
        if score:
            ranked_documents.append((score, document_id))

    ranked_documents.sort(reverse=True)
    return [document_id for _, document_id in ranked_documents[:MAX_DOCUMENT_CANDIDATES]]


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
        }
    )

    return response["choices"][0]["message"]["content"].strip()


def _run_pipeline(question, document_ids):
    contexts = _retrieve_context(question, document_ids)
    answer = (
        _answer_with_deepseek(question, contexts)
        if contexts
        else "I don't know based on the uploaded documents."
    )

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


def _hit(expected_document_id, retrieved_document_ids):
    if not expected_document_id:
        return None
    return expected_document_id in retrieved_document_ids


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

    expected_document_id = str(body.get("expected_document_id", "")).strip() or None

    candidate_document_ids = _select_candidate_document_ids(question)

    # Run both pipelines concurrently so the comparison's total latency is
    # close to a single query's, keeping it inside API Gateway's ~29s cap.
    with ThreadPoolExecutor(max_workers=2) as executor:
        filtered_future = executor.submit(_run_pipeline, question, candidate_document_ids or None)
        unfiltered_future = executor.submit(_run_pipeline, question, None)
        filtered = filtered_future.result()
        unfiltered = unfiltered_future.result()

    filtered["documents_selected"] = len(candidate_document_ids)
    filtered["correct_document_hit"] = _hit(expected_document_id, filtered["retrieved_document_ids"])
    unfiltered["correct_document_hit"] = _hit(expected_document_id, unfiltered["retrieved_document_ids"])

    record = {
        "eval_id": str(uuid.uuid4()),
        "question": question,
        "expected_document_id": expected_document_id,
        "filtered": filtered,
        "unfiltered": unfiltered,
        "context_token_reduction_percent": _reduction_percent(
            unfiltered["estimated_context_tokens"], filtered["estimated_context_tokens"]
        ),
        "created_at": _utc_now(),
    }

    eval_table.put_item(Item=json.loads(json.dumps(record), parse_float=Decimal))

    return _response(200, record)


def _summary(items):
    count = len(items)
    if count == 0:
        return {"count": 0}

    def avg(values):
        return round(sum(values) / len(values), 1) if values else 0

    def hit_rate(mode):
        hits = [
            item[mode]["correct_document_hit"]
            for item in items
            if item.get(mode, {}).get("correct_document_hit") is not None
        ]
        if not hits:
            return None
        return round(100 * sum(1 for hit in hits if hit) / len(hits))

    return {
        "count": count,
        "labeled_count": sum(1 for item in items if item.get("expected_document_id")),
        "avg_context_token_reduction_percent": avg(
            [item.get("context_token_reduction_percent", 0) for item in items]
        ),
        "avg_filtered_chunks": avg([item["filtered"]["retrieved_chunks"] for item in items]),
        "avg_unfiltered_chunks": avg([item["unfiltered"]["retrieved_chunks"] for item in items]),
        "avg_filtered_context_tokens": avg(
            [item["filtered"]["estimated_context_tokens"] for item in items]
        ),
        "avg_unfiltered_context_tokens": avg(
            [item["unfiltered"]["estimated_context_tokens"] for item in items]
        ),
        "filtered_hit_rate_percent": hit_rate("filtered"),
        "unfiltered_hit_rate_percent": hit_rate("unfiltered"),
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
