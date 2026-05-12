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
      uv pip install --quiet --python python3.12 --target ${var.build_dir} -r ${var.src_dir}/requirements.txt
      (cd ${var.src_dir} && find . -name '*.py' -type f -exec cp --parents '{}' ${var.build_dir}/ \;)
      cp -r ${var.shared_dir} ${var.build_dir}/shared
    EOT
  }
}

data "archive_file" "this" {
  type        = "zip"
  source_dir  = var.build_dir
  output_path = var.zip_path

  depends_on = [null_resource.build]
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  handler          = var.handler
  runtime          = var.runtime
  memory_size      = var.memory_size
  timeout          = var.timeout

  environment {
    variables = var.env_vars
  }
}
