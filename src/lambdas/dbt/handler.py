import os

import lambda_mp_patch
from shared import paths, report, s3_io, ssm, time_window

BUCKET = os.environ["BUCKET"]
KEY_SSM_PATH = os.environ["SNOWFLAKE_PRIVATE_KEY_SSM_PATH"]
PROJECT_DIR = os.path.join(os.environ["LAMBDA_TASK_ROOT"], "transform")


def lambda_handler(event, context) -> dict:
    key_path = "/tmp/transformer.p8"
    with open(key_path, "w") as f:
        f.write(ssm.get_parameter(KEY_SSM_PATH))
    os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"] = key_path
    os.environ["HOME"] = "/tmp"  # /var/task is read-only; dbt writes ~/.dbt

    lambda_mp_patch.apply()
    from dbt.cli.main import dbtRunner

    res = dbtRunner().invoke(
        [
            "build",
            "--project-dir", PROJECT_DIR,
            "--profiles-dir", PROJECT_DIR,
            "--target-path", "/tmp/target",
            "--log-path", "/tmp/logs",
        ]
    )

    today = time_window.today_utc()
    summary = report.build_dbt_report(today, res)
    # Report goes to S3 before raising, so the digest can read it even on failure.
    s3_io.put_json(BUCKET, paths.report_key("dbt", today), summary)

    if not res.success:
        raise RuntimeError(f"dbt build failed: {summary}")
    return summary
