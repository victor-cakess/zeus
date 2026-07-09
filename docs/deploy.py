"""Generate the Zeus build & deploy diagram (docs/deploy.png) with real cloud logos.

Companion to architecture.py: that one shows the platform at *runtime*, this one
shows how code *ships* — PR validation, the two packaging paths, and the CD pipeline.

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
from diagrams.saas.analytics import Snowflake

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
    # ---- PR validation: zero-copy clone CI (decision 15) --------------------
    # On PRs touching transform/**: build dbt against a throwaway clone of
    # ZEUS_DEV so prod is never touched, then drop it. Gates merge-to-dev.
    with Cluster("PR validation — clone CI (on PR, transform/**)"):
        clone_ci = GithubActions("dbt-clone-ci.yml")
        clone = Snowflake("ZEUS_CI_PR_<n>\nzero-copy clone of ZEUS_DEV\ndbt build → DROP")
        clone_ci >> Edge(label="CREATE CLONE →\ndbt build → DROP") >> clone

    # ---- Container path: dbt Lambda via GitHub Actions CD (decision 16) -----
    with Cluster("dbt Lambda — CD on merge to dev (GitHub Actions)"):
        gha = GithubActions("dbt-deploy.yml\ndocker build")
        oidc = IAMRole("zeus-dev-dbt-deploy\n(assumed via OIDC —\nno long-lived keys)")
        ecr = ECR("ECR repo\ntag = commit SHA")
        dbt_lambda = Lambda("zeus-dev-dbt-run\n(container image)")

        gha >> Edge(label="assume role") >> oidc
        oidc >> Edge(label="push image") >> ecr
        ecr >> Edge(label="update-function-code") >> dbt_lambda
        dbt_lambda >> Edge(label="smoke-invoke\n(deploy gate:\nfails on bad test)", style="dashed") >> gha

    # ---- Zip path: ingest (EIA/EIA_REGION/NOAA/FRED) + digest Lambdas ------
    with Cluster("Zip Lambdas — local build (Terraform apply)"):
        dev = User("developer\nuv pip install\n--target → zip")
        artifacts = S3("S3\nbuild artifacts\n(zip ~49 MiB)")
        zip_lambdas = Lambda("ingest + digest\nLambdas")

        dev >> Edge(label="upload") >> artifacts
        artifacts >> Edge(label="s3_key reference") >> zip_lambdas

    # Clone CI must pass, then the merge to dev triggers the image CD.
    clone >> Edge(label="merge to dev\nif green", style="bold") >> gha
