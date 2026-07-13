"""Generate the Zeus architecture diagram (docs/architecture.png) with real cloud logos.

Diagram-as-code via mingrammer/diagrams (https://diagrams.mingrammer.com/).
Requires Graphviz on PATH (`apt install graphviz` / `brew install graphviz`).

Regenerate:
    uv run --with diagrams --no-project python docs/architecture.py

`diagrams` is a docs-only generator, intentionally NOT a project dependency —
it is pulled in on demand via `uv run --with` so it never ships in any Lambda.
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSns, StepFunctions
from diagrams.aws.storage import S3
from diagrams.saas.analytics import Snowflake

graph_attr = {
    "fontsize": "16",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
    "nodesep": "0.8",
    "ranksep": "1.4",
}

# show=False: write the file, don't try to open a viewer.
# outformat png; filename has no extension (diagrams appends it).
with Diagram(
    "Zeus — daily energy data pipeline",
    filename="docs/architecture",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    cron = Eventbridge("EventBridge cron\n07:00 UTC daily")

    with Cluster("Step Functions — zeus-dev-daily-pipeline"):
        sfn = StepFunctions("daily-pipeline")

        with Cluster("Ingest (parallel)"):
            ingest = [
                Lambda("EIA\nhourly fuel-type"),
                Lambda("EIA region\ndemand + DA forecast"),
                Lambda("EIA interchange\nBA-to-BA flows"),
                Lambda("NOAA\ndaily weather"),
                Lambda("FRED\nenergy prices"),
            ]

        dbt = Lambda("dbt runner\n(container image)\nbuild + test")
        digest = Lambda("digest\n(always runs)")

    s3 = S3("S3\nraw / curated / reports")
    landing = Snowflake("Snowflake landing\nEIA / EIA_REGION /\nEIA_INTERCHANGE /\nNOAA / FRED _GRID")
    marts = Snowflake("Snowflake marts\nfct_* tables")
    sns = SimpleNotificationServiceSns("SNS\nemail digest + alerts")

    # control: cron starts the state machine, which fans out the ingest branches
    cron >> sfn >> ingest

    # data spine, left to right: land files → COPY into Snowflake → dbt → marts
    ingest >> Edge(label="raw JSON +\ncurated Parquet") >> s3
    s3 >> Edge(label="COPY INTO") >> landing
    landing >> Edge(label="read") >> dbt
    dbt >> Edge(label="staging →\nintermediate →\nmarts") >> marts

    # digest runs last and publishes; failures also alert via SNS
    dbt >> digest >> Edge(label="combined run-report\n+ failure alerts") >> sns
