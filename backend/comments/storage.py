import uuid
from functools import cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone


@cache
def _client():
    return boto3.session.Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        # OCI's S3 API accepts path-style addresses only and rejects the newer default checksums.
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 2},
        ),
    )


def put(file, key=None):
    """Stores a processed file and returns its key in the bucket."""
    key = key or f"attachments/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{file.extension}"
    _client().put_object(Bucket=settings.S3_BUCKET, Key=key, Body=file.content, ContentType=file.content_type)
    return key


def exists(key):
    try:
        _client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def delete(key):
    # The bucket answers success for a key that is already gone.
    _client().delete_object(Bucket=settings.S3_BUCKET, Key=key)


def create_bucket():
    """Creates the bucket unless it exists; returns whether it was created."""
    try:
        _client().head_bucket(Bucket=settings.S3_BUCKET)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
        _client().create_bucket(Bucket=settings.S3_BUCKET)
        return True
    return False
