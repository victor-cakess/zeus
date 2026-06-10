resource "null_resource" "build" {
  triggers = {
    requirements = filemd5("${var.src_dir}/requirements.txt")
    src_files    = sha1(join("", [for f in fileset(var.src_dir, "**/*.py") : filesha1("${var.src_dir}/${f}")]))
    shared_files = sha1(join("", [for f in fileset(var.shared_dir, "**/*.py") : filesha1("${var.shared_dir}/${f}")]))
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      unset VIRTUAL_ENV
      rm -rf ${var.build_dir}
      mkdir -p ${var.build_dir}
      BUILD_DIR="$(cd ${var.build_dir} && pwd)"
      uv pip install --quiet --python python3.12 --target "$BUILD_DIR" -r ${var.src_dir}/requirements.txt
      (cd ${var.src_dir} && find . -name '*.py' -type f -exec cp --parents '{}' "$BUILD_DIR/" \;)
      cp -r ${var.shared_dir} "$BUILD_DIR/shared"
    EOT
  }
}

data "archive_file" "this" {
  type        = "zip"
  source_dir  = var.build_dir
  output_path = var.zip_path

  depends_on = [null_resource.build]
}

# The package (pyarrow + snowflake-connector) is ~49 MiB zipped — too close to the
# 50 MiB direct-upload limit, so deploy via S3 instead. source_hash, not etag: the
# provider uploads files this large via multipart, whose S3 etag never equals the
# plain md5, so etag-based change detection re-uploads on every apply.
resource "aws_s3_object" "this" {
  bucket = var.artifact_bucket
  key    = "lambda-artifacts/${var.name}.zip"
  source = data.archive_file.this.output_path

  source_hash = data.archive_file.this.output_md5
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  s3_bucket        = aws_s3_object.this.bucket
  s3_key           = aws_s3_object.this.key
  source_code_hash = data.archive_file.this.output_base64sha256
  handler          = var.handler
  runtime          = var.runtime
  memory_size      = var.memory_size
  timeout          = var.timeout

  environment {
    variables = var.env_vars
  }
}
