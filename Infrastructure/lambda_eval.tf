data "archive_file" "eval" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/eval/app.py"
  output_path = "${path.module}/.terraform/eval.zip"
}

resource "aws_lambda_function" "eval" {
  function_name = "${var.project_name}-${var.environment}-eval"
  role          = aws_iam_role.eval.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  timeout       = 28
  memory_size   = 512

  filename         = data.archive_file.eval.output_path
  source_code_hash = data.archive_file.eval.output_base64sha256

  environment {
    variables = {
      DOCUMENTS_TABLE             = aws_dynamodb_table.documents.name
      EVAL_RESULTS_TABLE          = aws_dynamodb_table.eval_results.name
      DEEPSEEK_API_KEY_SECRET_ARN = aws_secretsmanager_secret.deepseek_api_key.arn
      DEEPSEEK_API_URL            = var.deepseek_api_url
      DEEPSEEK_MODEL              = var.deepseek_model
      KNOWLEDGE_BASE_ID           = aws_bedrockagent_knowledge_base.documents.id
      MAX_RETRIEVAL_RESULTS       = "5"
      MAX_DOCUMENT_CANDIDATES     = "3"
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-eval"
  }
}
