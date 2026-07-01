import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
bedrock_agent_client = boto3.client("bedrock-agent")

DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
KNOWLEDGE_BASE_DATA_SOURCE_ID = os.environ["KNOWLEDGE_BASE_DATA_SOURCE_ID"]

READY_STATUSES = {"READY_FOR_INGESTION", "INGESTION_RETRY_REQUIRED"}
TRACKED_STATUSES = {"INGESTION_STARTED", "INGESTION_IN_PROGRESS"}
ACTIVE_JOB_STATUSES = {"STARTING", "IN_PROGRESS", "STOPPING"}
SUCCESS_JOB_STATUSES = {"COMPLETE", "COMPLETED"}
FAILED_JOB_STATUSES = {"FAILED", "STOPPED"}

table = dynamodb.Table(DOCUMENTS_TABLE)


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _scan_documents():
    items = []
    response = table.scan()

    while True:
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        response = table.scan(ExclusiveStartKey=last_key)

    return items


def _get_ingestion_job(job_id):
    response = bedrock_agent_client.get_ingestion_job(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        dataSourceId=KNOWLEDGE_BASE_DATA_SOURCE_ID,
        ingestionJobId=job_id,
    )
    return response["ingestionJob"]


def _list_latest_ingestion_jobs(max_results=10):
    response = bedrock_agent_client.list_ingestion_jobs(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        dataSourceId=KNOWLEDGE_BASE_DATA_SOURCE_ID,
        maxResults=max_results,
    )
    jobs = response.get("ingestionJobSummaries", [])
    jobs.sort(key=lambda job: str(job.get("startedAt") or ""), reverse=True)
    return jobs


def _find_active_ingestion_job():
    for job in _list_latest_ingestion_jobs():
        if job.get("status") in ACTIVE_JOB_STATUSES:
            return job
    return None


def _update_documents_for_job(document_ids, status, job_id=None, failure_reason=None):
    now = _utc_now()
    updated = 0

    for document_id in document_ids:
        expression_parts = [
            "knowledge_base_status = :knowledge_base_status",
            "knowledge_base_id = :knowledge_base_id",
            "knowledge_base_data_source_id = :knowledge_base_data_source_id",
            "updated_at = :updated_at",
        ]
        values = {
            ":knowledge_base_status": status,
            ":knowledge_base_id": KNOWLEDGE_BASE_ID,
            ":knowledge_base_data_source_id": KNOWLEDGE_BASE_DATA_SOURCE_ID,
            ":updated_at": now,
        }

        if job_id:
            expression_parts.append("knowledge_base_ingestion_job_id = :job_id")
            values[":job_id"] = job_id

        if failure_reason:
            expression_parts.append("knowledge_base_failure_reason = :failure_reason")
            values[":failure_reason"] = failure_reason[:1000]

        table.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET " + ", ".join(expression_parts),
            ExpressionAttributeValues=values,
        )
        updated += 1

    return updated


def _refresh_tracked_documents(items):
    tracked_job_ids = sorted(
        {
            item.get("knowledge_base_ingestion_job_id")
            for item in items
            if item.get("knowledge_base_status") in TRACKED_STATUSES
            and item.get("knowledge_base_ingestion_job_id")
        }
    )

    refreshed = {}
    updated = 0

    for job_id in tracked_job_ids:
        job = _get_ingestion_job(job_id)
        job_status = job.get("status")
        refreshed[job_id] = job_status

        document_ids = [
            item["document_id"]
            for item in items
            if item.get("knowledge_base_ingestion_job_id") == job_id
            and item.get("knowledge_base_status") in TRACKED_STATUSES
        ]

        if job_status in SUCCESS_JOB_STATUSES:
            updated += _update_documents_for_job(document_ids, "SYNCED", job_id)
        elif job_status in FAILED_JOB_STATUSES:
            failure_reasons = job.get("failureReasons") or ["Knowledge Base ingestion failed."]
            failure_reason = failure_reasons[0]
            updated += _update_documents_for_job(
                document_ids, "INGESTION_FAILED", job_id, failure_reason
            )
        elif job_status in ACTIVE_JOB_STATUSES:
            updated += _update_documents_for_job(document_ids, "INGESTION_IN_PROGRESS", job_id)

    return {"refreshed_jobs": refreshed, "documents_updated": updated}


def _ready_documents(items):
    return [
        item
        for item in items
        if item.get("metadata_status") == "COMPLETED"
        and item.get("knowledge_base_status") in READY_STATUSES
    ]


def _sync_status():
    items = _scan_documents()
    refresh = _refresh_tracked_documents(items)
    items = _scan_documents()
    active_job = _find_active_ingestion_job()
    ready = _ready_documents(items)

    counts = {}
    for item in items:
        status = item.get("knowledge_base_status", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1

    return {
        "knowledge_base_id": KNOWLEDGE_BASE_ID,
        "knowledge_base_data_source_id": KNOWLEDGE_BASE_DATA_SOURCE_ID,
        "active_ingestion_job": active_job,
        "ready_for_ingestion_count": len(ready),
        "status_counts": counts,
        "refresh": refresh,
    }


def _start_sync():
    items = _scan_documents()
    refresh = _refresh_tracked_documents(items)
    items = _scan_documents()
    active_job = _find_active_ingestion_job()

    if active_job:
        return _response(
            202,
            {
                "message": "Knowledge Base ingestion is already running.",
                "active_ingestion_job": active_job,
                "refresh": refresh,
            },
        )

    ready = _ready_documents(items)
    if not ready:
        return _response(
            200,
            {
                "message": "No documents are ready for Knowledge Base ingestion.",
                "sync_status": _sync_status(),
            },
        )

    try:
        response = bedrock_agent_client.start_ingestion_job(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            dataSourceId=KNOWLEDGE_BASE_DATA_SOURCE_ID,
            description=f"Batch ingest {len(ready)} ready Aethon documents",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        return _response(
            202,
            {
                "message": "Knowledge Base became busy before a new sync could start.",
                "active_ingestion_job": _find_active_ingestion_job(),
            },
        )

    ingestion_job = response["ingestionJob"]
    ingestion_job_id = ingestion_job["ingestionJobId"]
    ingestion_status = ingestion_job["status"]
    document_ids = [item["document_id"] for item in ready]
    updated = _update_documents_for_job(
        document_ids,
        "INGESTION_STARTED",
        ingestion_job_id,
    )

    return _response(
        202,
        {
            "message": "Knowledge Base ingestion started.",
            "knowledge_base_ingestion_job_id": ingestion_job_id,
            "knowledge_base_status": ingestion_status,
            "documents_marked_for_ingestion": updated,
        },
    )


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    try:
        if method == "POST":
            return _start_sync()
        if method == "GET":
            return _response(200, _sync_status())
        return _response(405, {"message": "Method not allowed."})
    except Exception as error:
        logger.exception("Knowledge Base sync request failed")
        return _response(500, {"message": str(error)})
