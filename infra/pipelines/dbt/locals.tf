locals {
  project = "zeus"
  env     = "dev"

  prefix                   = "${local.project}-${local.env}"
  function_name            = "${local.prefix}-dbt-run"
  transformer_key_ssm_path = "/${local.project}/${local.env}/snowflake/transformer_private_key"

  bucket_name = data.terraform_remote_state.core.outputs.bucket_name
  bucket_arn  = data.terraform_remote_state.core.outputs.bucket_arn

  reports_arn_pattern = "${local.bucket_arn}/reports/dbt/*"

  # Docker build context = repo root, so the Dockerfile can COPY transform/ and src/.
  repo_root     = abspath("${path.module}/../../..")
  dbt_src       = "${local.repo_root}/src/lambdas/dbt"
  transform_dir = "${local.repo_root}/transform"

  # Image fingerprint — any change to the runner code, the dbt project, or the
  # shared helpers forces a rebuild + push (mirrors lambda_job's build triggers).
  # dbt_packages/ and target/ are local artifacts, deliberately not fingerprinted.
  image_hash = sha1(join("", concat(
    [
      filesha1("${local.dbt_src}/Dockerfile"),
      filesha1("${local.dbt_src}/handler.py"),
      filesha1("${local.dbt_src}/requirements.txt"),
      filesha1("${local.transform_dir}/dbt_project.yml"),
      filesha1("${local.transform_dir}/profiles.yml"),
      filesha1("${local.transform_dir}/packages.yml"),
    ],
    [for f in sort(fileset("${local.transform_dir}/models", "**/*.{sql,yml}")) : filesha1("${local.transform_dir}/models/${f}")],
    [for f in sort(fileset("${local.transform_dir}/macros", "**/*.sql")) : filesha1("${local.transform_dir}/macros/${f}")],
    [for f in sort(fileset("${local.transform_dir}/tests", "**/*.{sql,yml}")) : filesha1("${local.transform_dir}/tests/${f}")],
    [for f in sort(fileset("${local.transform_dir}/seeds", "**/*.{csv,yml}")) : filesha1("${local.transform_dir}/seeds/${f}")],
    [for f in sort(fileset("${local.repo_root}/src/shared", "**/*.py")) : filesha1("${local.repo_root}/src/shared/${f}")],
  )))

  registry = split("/", aws_ecr_repository.dbt.repository_url)[0]
}
