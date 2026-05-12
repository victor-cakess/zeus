import boto3

_ssm = boto3.client("ssm")
_cache: dict[str, str] = {}


def get_parameter(name: str) -> str:
    if name not in _cache:
        resp = _ssm.get_parameter(Name=name, WithDecryption=True)
        _cache[name] = resp["Parameter"]["Value"]
    return _cache[name]
