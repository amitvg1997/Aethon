import json
import logging
import os
import re
import urllib.error
import urllib.request

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
KNOWLEDGE_BASE_CHUNK_MAX_TOKENS = int(
    os.environ.get("KNOWLEDGE_BASE_CHUNK_MAX_TOKENS", "500")
)
MAX_DOCUMENT_CANDIDATES = int(os.environ.get("MAX_DOCUMENT_CANDIDATES", "3"))
SYNCED_KNOWLEDGE_BASE_STATUSES = {"SYNCED", "COMPLETE", "COMPLETED"}
_deepseek_api_key = None
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
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_get_deepseek_api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "Aethon/1.0 (AWS Lambda document query)",
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
        if item.get("knowledge_base_status") not in SYNCED_KNOWLEDGE_BASE_STATUSES:
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


def _usage_summary(candidate_document_ids, contexts):
    context_tokens = sum(_estimate_tokens(context["text"]) for context in contexts)
    unfiltered_upper_bound = MAX_RETRIEVAL_RESULTS * KNOWLEDGE_BASE_CHUNK_MAX_TOKENS
    reduction_percent = 0
    if unfiltered_upper_bound:
        reduction_percent = round(
            max(0, 1 - (context_tokens / unfiltered_upper_bound)) * 100
        )

    return {
        "documents_selected": len(candidate_document_ids),
        "retrieved_chunks": len(contexts),
        "estimated_context_tokens": context_tokens,
        "unfiltered_context_upper_bound_tokens": unfiltered_upper_bound,
        "potential_context_token_reduction_percent": reduction_percent,
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
            "max_tokens": 1800,
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
        candidate_document_ids = _select_candidate_document_ids(question)

        # Fall back to unfiltered retrieval when no DynamoDB candidates match —
        # prevents recall collapse on ambiguous or short queries.
        contexts = _retrieve_context(question, candidate_document_ids or None)

        if not contexts:
            return _response(
                200,
                {
                    "answer": "I don't know based on the uploaded documents.",
                    "citations": [],
                    "retrieved_count": 0,
                    "usage_summary": _usage_summary(candidate_document_ids, []),
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
            "usage_summary": _usage_summary(candidate_document_ids, contexts),
        },
    )
