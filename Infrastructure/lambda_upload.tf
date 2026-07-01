data "archive_file" "upload_url" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/upload_url/app.py"
  output_path = "${path.module}/.terraform/upload_url.zip"
}

resource "aws_lambda_function" "upload_url" {
  function_name = "${var.project_name}-${var.environment}-upload-url"
  role          = aws_iam_role.upload_url.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"

  filename         = data.archive_file.upload_url.output_path
  source_code_hash = data.archive_file.upload_url.output_base64sha256

  environment {
    variables = {
      DOCUMENTS_BUCKET  = aws_s3_bucket.documents.id
      AETHON_AWS_REGION = var.aws_region
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-upload-url"
  }
}
