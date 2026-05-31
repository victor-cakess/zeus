import json
from typing import Iterator

import boto3

_s3 = boto3.client("s3")


def put_json(bucket: str, key: str, obj) -> None:
    _s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(obj).encode("utf-8"),
        ContentType="application/json",
    )


def put_bytes(bucket: str, key: str, body: bytes) -> None:
    _s3.put_object(Bucket=bucket, Key=key, Body=body)


def list_keys(bucket: str, prefix: str) -> list[str]:
    paginator = _s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def get_json(bucket: str, key: str):
    body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def iter_objects(bucket: str, prefix: str) -> Iterator:
    """Yield each object's parsed JSON as-is. The caller owns the file's shape."""
    for key in list_keys(bucket, prefix):
        yield get_json(bucket, key)
