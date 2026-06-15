import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePath
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
textract_client = boto3.client("textract")

DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
TEXTRACT_SNS_TOPIC_ARN = os.environ["TEXTRACT_SNS_TOPIC_ARN"]
TEXTRACT_ROLE_ARN = os.environ["TEXTRACT_ROLE_ARN"]
table = dynamodb.Table(DOCUMENTS_TABLE)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _document_id_from_key(object_key):
    parts = PurePath(object_key).parts

    if len(parts) >= 2 and parts[0] == "uploads":
        return parts[1]

    return str(uuid.uuid5(uuid.NAMESPACE_URL, object_key))


def _filename_from_key(object_key):
    return PurePath(object_key).name


def _extension_from_filename(filename):
    suffix = PurePath(filename).suffix
    return suffix.lower().lstrip(".")


def _build_initial_document_item(bucket_name, object_key):
    filename = _filename_from_key(object_key)
    now = _utc_now()

    return {
        "document_id": _document_id_from_key(object_key),
        "bucket_name": bucket_name,
        "object_key": object_key,
        "filename": filename,
        "file_extension": _extension_from_filename(filename),
        "processing_status": "RECEIVED",
        "metadata_status": "PENDING",
        "knowledge_base_status": "PENDING",
        "created_at": now,
        "updated_at": now,
    }


def _store_initial_document_record(item):
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(document_id)",
        )
        return "CREATED"
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

        table.update_item(
            Key={"document_id": item["document_id"]},
            UpdateExpression=(
                "SET bucket_name = :bucket_name, "
                "object_key = :object_key, "
                "filename = :filename, "
                "file_extension = :file_extension, "
                "updated_at = :updated_at"
            ),
            ExpressionAttributeValues={
                ":bucket_name": item["bucket_name"],
                ":object_key": item["object_key"],
                ":filename": item["filename"],
                ":file_extension": item["file_extension"],
                ":updated_at": item["updated_at"],
            },
        )
        return "UPDATED"


def _start_textract_job(item):
    response = textract_client.start_document_text_detection(
        DocumentLocation={
            "S3Object": {
                "Bucket": item["bucket_name"],
                "Name": item["object_key"],
            }
        },
        ClientRequestToken=item["document_id"],
        JobTag=item["document_id"],
        NotificationChannel={
            "SNSTopicArn": TEXTRACT_SNS_TOPIC_ARN,
            "RoleArn": TEXTRACT_ROLE_ARN,
        },
    )

    textract_job_id = response["JobId"]
    table.update_item(
        Key={"document_id": item["document_id"]},
        UpdateExpression=(
            "SET textract_job_id = :textract_job_id, "
            "processing_status = :processing_status, "
            "metadata_status = :metadata_status, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":textract_job_id": textract_job_id,
            ":processing_status": "TEXTRACT_STARTED",
            ":metadata_status": "TEXTRACT_IN_PROGRESS",
            ":updated_at": _utc_now(),
        },
    )

    return textract_job_id


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    processed_documents = []

    for record in event.get("Records", []):
        if not record.get("eventName", "").startswith("ObjectCreated:"):
            continue

        bucket_name = record["s3"]["bucket"]["name"]
        object_key = unquote_plus(record["s3"]["object"]["key"])

        item = _build_initial_document_item(bucket_name, object_key)

        write_status = _store_initial_document_record(item)
        textract_job_id = _start_textract_job(item)

        processed_documents.append(
            {
                "document_id": item["document_id"],
                "bucket_name": bucket_name,
                "object_key": object_key,
                "write_status": write_status,
                "textract_job_id": textract_job_id,
            }
        )

    return {
        "processed_count": len(processed_documents),
        "processed_documents": processed_documents,
    }
