data "archive_file" "memory_optimizer" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/memory_optimizer/app.py"
  output_path = "${path.module}/.terraform/memory_optimizer.zip"
}

resource "aws_lambda_function" "memory_optimizer" {
  function_name = "${var.project_name}-${var.environment}-memory-optimizer"
  role          = aws_iam_role.memory_optimizer.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  timeout       = 60

  filename         = data.archive_file.memory_optimizer.output_path
  source_code_hash = data.archive_file.memory_optimizer.output_base64sha256

  environment {
    variables = {
      DOCUMENTS_TABLE        = aws_dynamodb_table.documents.name
      TEXTRACT_ROLE_ARN      = aws_iam_role.textract_sns_publish.arn
      TEXTRACT_SNS_TOPIC_ARN = aws_sns_topic.textract_results.arn
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-memory-optimizer"
  }
}
