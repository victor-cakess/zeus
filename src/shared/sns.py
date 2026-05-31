import boto3

_sns = boto3.client("sns")


def publish(topic_arn: str, subject: str, message: str) -> None:
    _sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)
