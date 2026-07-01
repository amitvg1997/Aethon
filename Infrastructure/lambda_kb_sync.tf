data "archive_file" "kb_sync" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/kb_sync/app.py"
  output_path = "${path.module}/.terraform/kb_sync.zip"
}

resource "aws_lambda_function" "kb_sync" {
  function_name = "${var.project_name}-${var.environment}-kb-sync"
  role          = aws_iam_role.kb_sync.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  timeout       = 28
  memory_size   = 512

  filename         = data.archive_file.kb_sync.output_path
  source_code_hash = data.archive_file.kb_sync.output_base64sha256

  environment {
    variables = {
      DOCUMENTS_TABLE               = aws_dynamodb_table.documents.name
      KNOWLEDGE_BASE_ID             = aws_bedrockagent_knowledge_base.documents.id
      KNOWLEDGE_BASE_DATA_SOURCE_ID = aws_bedrockagent_data_source.documents.data_source_id
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-kb-sync"
  }
}
