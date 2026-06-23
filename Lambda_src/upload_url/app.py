import json
import os
import uuid
from pathlib import PurePath

import boto3
from botocore.config import Config

AWS_REGION = os.environ["AWS_REGION"]
s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(s3={"addressing_style": "virtual"}),
)
DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]
UPLOAD_URL_EXPIRATION_SECONDS = 300


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"message": "Request body must be valid JSON."})

    filename = PurePath(str(body.get("filename", "")).replace("\\", "/")).name
    content_type = str(body.get("content_type", "")).strip()

    if not filename or filename in {".", ".."}:
        return _response(400, {"message": "filename is required."})

    object_key = f"uploads/{uuid.uuid4()}/{filename}"
    put_object_params = {
        "Bucket": DOCUMENTS_BUCKET,
        "Key": object_key,
    }

    if content_type:
        put_object_params["ContentType"] = content_type

    upload_url = s3_client.generate_presigned_url(
        ClientMethod="put_object",
        Params=put_object_params,
        ExpiresIn=UPLOAD_URL_EXPIRATION_SECONDS,
    )

    return _response(
        200,
        {
            "object_key": object_key,
            "upload_url": upload_url,
            "expires_in": UPLOAD_URL_EXPIRATION_SECONDS,
        },
    )
