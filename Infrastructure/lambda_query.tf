data "archive_file" "query" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/query/app.py"
  output_path = "${path.module}/.terraform/query.zip"
}

resource "aws_lambda_function" "query" {
  function_name = "${var.project_name}-${var.environment}-query"
  role          = aws_iam_role.query.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  timeout       = 90
  memory_size   = 512

  filename         = data.archive_file.query.output_path
  source_code_hash = data.archive_file.query.output_base64sha256

  environment {
    variables = {
      DEEPSEEK_API_KEY_SECRET_ARN = aws_secretsmanager_secret.deepseek_api_key.arn
      DEEPSEEK_API_URL            = var.deepseek_api_url
      DEEPSEEK_MODEL              = var.deepseek_model
      KNOWLEDGE_BASE_ID           = aws_bedrockagent_knowledge_base.documents.id
      MAX_RETRIEVAL_RESULTS       = "5"
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-query"
  }
}
