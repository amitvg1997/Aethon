import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
textract_client = boto3.client("textract")
s3_client = boto3.client("s3")
secrets_client = boto3.client("secretsmanager")

DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]
DEEPSEEK_API_KEY_SECRET_ARN = os.environ["DEEPSEEK_API_KEY_SECRET_ARN"]
DEEPSEEK_API_URL = os.environ["DEEPSEEK_API_URL"]
DEEPSEEK_MODEL = os.environ["DEEPSEEK_MODEL"]
TEXT_PREVIEW_LIMIT = 3000
LLM_INPUT_LIMIT = 18000

table = dynamodb.Table(DOCUMENTS_TABLE)
_deepseek_api_key = None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _parse_sns_message(record):
    return json.loads(record["Sns"]["Message"])


def _get_textract_lines(job_id):
    lines = []
    next_token = None

    while True:
        request = {"JobId": job_id}

        if next_token:
            request["NextToken"] = next_token

        response = textract_client.get_document_text_detection(**request)

        for block in response.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                lines.append(block["Text"])

        next_token = response.get("NextToken")

        if not next_token:
            break

    return lines


def _store_extracted_text(document_id, extracted_text):
    object_key = f"extracted-text/{document_id}.txt"

    s3_client.put_object(
        Bucket=DOCUMENTS_BUCKET,
        Key=object_key,
        Body=extracted_text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )

    return object_key


def _store_knowledge_base_metadata(document_id, object_key, metadata):
    metadata_key = f"{object_key}.metadata.json"
    sidecar = {
        "metadataAttributes": {
            "document_id": document_id,
            "document_type": metadata["document_type"],
            "title": metadata["title"],
        }
    }

    s3_client.put_object(
        Bucket=DOCUMENTS_BUCKET,
        Key=metadata_key,
        Body=json.dumps(sidecar).encode("utf-8"),
        ContentType="application/json",
    )

    return metadata_key


def _normalized_content_hash(extracted_text):
    """Content fingerprint tolerant of OCR whitespace/casing jitter."""
    normalized = " ".join(extracted_text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _find_canonical_document(document_id, content_sha256):
    """Return the earliest other document with identical extracted content.

    Only non-duplicate documents qualify as canonical, so duplicates never
    chain to each other. A scan is fine at prototype scale (~thousands of
    docs); two identical files processed at the same instant can race past
    each other, which is an accepted prototype limitation.
    """
    matches = []
    scan_kwargs = {
        "FilterExpression": (
            Attr("content_sha256").eq(content_sha256)
            & Attr("document_id").ne(document_id)
            & Attr("duplicate_of").not_exists()
        ),
        "ProjectionExpression": "document_id, created_at",
    }
    response = table.scan(**scan_kwargs)

    while True:
        matches.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = table.scan(**scan_kwargs, ExclusiveStartKey=last_key)

    if not matches:
        return None

    return min(matches, key=lambda item: str(item.get("created_at", "")))["document_id"]


def _mark_document_duplicate(
    document_id, job_id, extracted_text_key, canonical_document_id, content_sha256
):
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET textract_job_id = :textract_job_id, "
            "extracted_text_s3_key = :extracted_text_s3_key, "
            "content_sha256 = :content_sha256, "
            "duplicate_of = :duplicate_of, "
            "processing_status = :processing_status, "
            "metadata_status = :metadata_status, "
            "knowledge_base_status = :knowledge_base_status, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":textract_job_id": job_id,
            ":extracted_text_s3_key": extracted_text_key,
            ":content_sha256": content_sha256,
            ":duplicate_of": canonical_document_id,
            ":processing_status": "DUPLICATE",
            ":metadata_status": "SKIPPED_DUPLICATE",
            ":knowledge_base_status": "SKIPPED_DUPLICATE",
            ":updated_at": _utc_now(),
        },
    )


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
            "User-Agent": "Aethon/1.0 (AWS Lambda metadata extraction)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API request failed: {error.code} {error_body}") from error


def _extract_json_object(content):
    content = content.strip()

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced_match:
        content = fenced_match.group(1)
    elif not content.startswith("{"):
        # Some models prose-wrap the JSON even when asked not to; fall back
        # to the outermost {...} span rather than failing the whole document.
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            content = brace_match.group(0)

    return json.loads(content)


def _extract_metadata_with_deepseek(extracted_text):
    system_prompt = """
You are Aethon's document memory metadata extractor.
Return only valid json, with no markdown code fences and no commentary.
Do not invent information that is not supported by the document text.
""".strip()

    user_prompt = f"""
Extract structured metadata from the document text.

Return json with this exact structure:
{{
  "title": "short document title",
  "summary": "2-4 sentence summary",
  "topics": ["topic 1", "topic 2"],
  "facts": ["fact 1", "fact 2"],
  "document_type": "policy/report/manual/invoice/contract/other"
}}

Document text:
{extracted_text[:LLM_INPUT_LIMIT]}
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
            "max_tokens": 1500,
            "preference": "balanced",
        }
    )

    content = response["choices"][0]["message"]["content"]
    metadata = _extract_json_object(content)

    return {
        "title": str(metadata.get("title", "")).strip(),
        "summary": str(metadata.get("summary", "")).strip(),
        "topics": [
            str(topic).strip()
            for topic in metadata.get("topics", [])
            if str(topic).strip()
        ],
        "facts": [
            str(fact).strip()
            for fact in metadata.get("facts", [])
            if str(fact).strip()
        ],
        "document_type": str(metadata.get("document_type", "other")).strip() or "other",
    }


def _mark_document_failed(document_id, message):
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET processing_status = :processing_status, "
            "metadata_status = :metadata_status, "
            "failure_reason = :failure_reason, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":processing_status": "FAILED",
            ":metadata_status": "FAILED",
            ":failure_reason": message[:1000],
            ":updated_at": _utc_now(),
        },
    )


def _update_document_metadata(
    document_id,
    job_id,
    extracted_text_key,
    knowledge_base_metadata_key,
    text_preview,
    metadata,
    content_sha256,
):
    table.update_item(
        Key={"document_id": document_id},
        UpdateExpression=(
            "SET textract_job_id = :textract_job_id, "
            "extracted_text_s3_key = :extracted_text_s3_key, "
            "knowledge_base_metadata_s3_key = :knowledge_base_metadata_s3_key, "
            "content_sha256 = :content_sha256, "
            "text_preview = :text_preview, "
            "title = :title, "
            "summary = :summary, "
            "topics = :topics, "
            "facts = :facts, "
            "document_type = :document_type, "
            "processing_status = :processing_status, "
            "metadata_status = :metadata_status, "
            "knowledge_base_status = :knowledge_base_status, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":textract_job_id": job_id,
            ":extracted_text_s3_key": extracted_text_key,
            ":knowledge_base_metadata_s3_key": knowledge_base_metadata_key,
            ":content_sha256": content_sha256,
            ":text_preview": text_preview,
            ":title": metadata["title"],
            ":summary": metadata["summary"],
            ":topics": metadata["topics"],
            ":facts": metadata["facts"],
            ":document_type": metadata["document_type"],
            ":processing_status": "METADATA_EXTRACTED",
            ":metadata_status": "COMPLETED",
            ":knowledge_base_status": "READY_FOR_INGESTION",
            ":updated_at": _utc_now(),
        },
    )


def _process_textract_completion(message):
    job_id = message["JobId"]
    status = message["Status"]
    document_id = message["JobTag"]

    if status != "SUCCEEDED":
        _mark_document_failed(document_id, f"Textract job {job_id} finished with status {status}.")
        return {"document_id": document_id, "textract_job_id": job_id, "status": status}

    extracted_text = "\n".join(_get_textract_lines(job_id))
    extracted_text_key = _store_extracted_text(document_id, extracted_text)

    # Exact-duplicate detection runs BEFORE the LLM call: a redundant copy
    # costs no metadata-extraction tokens, is never written as a Knowledge
    # Base sidecar, and never enters ingestion — duplicates cannot pollute
    # the vector store or the retrieval context.
    content_sha256 = _normalized_content_hash(extracted_text)
    canonical_document_id = _find_canonical_document(document_id, content_sha256)
    if canonical_document_id:
        _mark_document_duplicate(
            document_id, job_id, extracted_text_key, canonical_document_id, content_sha256
        )
        return {
            "document_id": document_id,
            "textract_job_id": job_id,
            "duplicate_of": canonical_document_id,
            "status": "SKIPPED_DUPLICATE",
        }

    metadata = _extract_metadata_with_deepseek(extracted_text)
    document_key = message["DocumentLocation"]["S3ObjectName"]
    knowledge_base_metadata_key = _store_knowledge_base_metadata(
        document_id, document_key, metadata
    )

    _update_document_metadata(
        document_id=document_id,
        job_id=job_id,
        extracted_text_key=extracted_text_key,
        knowledge_base_metadata_key=knowledge_base_metadata_key,
        text_preview=extracted_text[:TEXT_PREVIEW_LIMIT],
        metadata=metadata,
        content_sha256=content_sha256,
    )
    return {
        "document_id": document_id,
        "textract_job_id": job_id,
        "knowledge_base_status": "READY_FOR_INGESTION",
        "status": "METADATA_EXTRACTED",
    }


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    processed_jobs = []

    for record in event.get("Records", []):
        message = None
        try:
            message = _parse_sns_message(record)
            processed_jobs.append(_process_textract_completion(message))
        except Exception as error:
            # Isolate failures per-record: one bad message (e.g. a transient
            # DeepSeek/Bedrock error) must not abort the whole invocation —
            # that would leave other records in this batch unprocessed and,
            # on Lambda's automatic retry, cause already-succeeded records to
            # be reprocessed (duplicate LLM calls, duplicate ingestion jobs).
            logger.exception("Failed to process Textract result record")
            document_id = (message or {}).get("JobTag")
            if document_id:
                try:
                    _mark_document_failed(document_id, f"Unhandled processing error: {error}")
                except Exception:
                    logger.exception("Failed to mark document %s as failed", document_id)
            processed_jobs.append(
                {"document_id": document_id, "status": "ERROR", "error": str(error)}
            )

    return {
        "processed_count": len(processed_jobs),
        "processed_jobs": processed_jobs,
    }
