import json
import logging
import os
import urllib.error
import urllib.request

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
secrets_client = boto3.client("secretsmanager")

DEEPSEEK_API_KEY_SECRET_ARN = os.environ["DEEPSEEK_API_KEY_SECRET_ARN"]
DEEPSEEK_API_URL = os.environ["DEEPSEEK_API_URL"]
DEEPSEEK_MODEL = os.environ["DEEPSEEK_MODEL"]
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
MAX_RETRIEVAL_RESULTS = int(os.environ.get("MAX_RETRIEVAL_RESULTS", "5"))
_deepseek_api_key = None


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
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API request failed: {error.code} {error_body}") from error


def _retrieve_context(question):
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": MAX_RETRIEVAL_RESULTS,
            },
        },
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
        contexts = _retrieve_context(question)
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
        },
    )
