"""Generate the Zeus build & deploy diagram (docs/deploy.png) with real cloud logos.

Companion to architecture.py: that one shows the platform at *runtime*, this one
shows how code *ships* — the two packaging paths and the CD pipeline.

Diagram-as-code via mingrammer/diagrams (https://diagrams.mingrammer.com/).
Requires Graphviz on PATH (`apt install graphviz` / `brew install graphviz`).

Regenerate:
    uv run --with diagrams --no-project python docs/deploy.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, Lambda
from diagrams.aws.security import IAMRole
from diagrams.aws.storage import S3
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import User

graph_attr = {
    "fontsize": "16",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
    "nodesep": "0.8",
    "ranksep": "1.4",
}

with Diagram(
    "Zeus — build & deploy",
    filename="docs/deploy",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    # ---- Zip path: ingest (EIA/NOAA/FRED) + digest Lambdas -------------------
    with Cluster("Zip Lambdas — local build (Terraform apply)"):
        dev = User("developer\nuv pip install\n--target → zip")
        artifacts = S3("S3\nbuild artifacts\n(zip ~49 MiB)")
        zip_lambdas = Lambda("ingest + digest\nLambdas")

        dev >> Edge(label="upload") >> artifacts
        artifacts >> Edge(label="s3_key reference") >> zip_lambdas

    # ---- Container path: dbt Lambda via GitHub Actions CD (Phase 2.5) --------
    with Cluster("dbt Lambda — CD on merge to dev (GitHub Actions)"):
        gha = GithubActions("dbt-deploy.yml\ndocker build")
        oidc = IAMRole("zeus-dev-dbt-deploy\n(assumed via OIDC —\nno long-lived keys)")
        ecr = ECR("ECR repo\ntag = commit SHA")
        dbt_lambda = Lambda("zeus-dev-dbt-run\n(container image)")

        gha >> Edge(label="assume role") >> oidc
        oidc >> Edge(label="push image") >> ecr
        ecr >> Edge(label="update-function-code") >> dbt_lambda
        dbt_lambda >> Edge(label="smoke-invoke\n(deploy gate:\nfails on bad test)", style="dashed") >> gha
