# dbt runner image. Build context = repo root (it COPYs transform/ and src/):
#   docker build --platform linux/amd64 -f src/lambdas/dbt/Dockerfile .
# Image contents are declared in three places — this file's COPYs, the repo-root
# .dockerignore allowlist, and the image_hash fingerprint in
# infra/pipelines/dbt/locals.tf. Keep them in sync.
FROM public.ecr.aws/lambda/python:3.12

COPY src/lambdas/dbt/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# dbt project + packages baked at build time — the runtime never hits the package hub.
# dbt deps needs no connection; the dummy env vars just satisfy profile parsing.
COPY transform/ ${LAMBDA_TASK_ROOT}/transform/
RUN SNOWFLAKE_ACCOUNT=build SNOWFLAKE_PRIVATE_KEY_FILE=/dev/null \
    dbt deps --project-dir ${LAMBDA_TASK_ROOT}/transform --profiles-dir ${LAMBDA_TASK_ROOT}/transform

COPY src/shared/ ${LAMBDA_TASK_ROOT}/shared/
COPY src/lambdas/dbt/*.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
