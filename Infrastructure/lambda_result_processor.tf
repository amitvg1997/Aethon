data "archive_file" "result_processor" {
  type        = "zip"
  source_file = "${path.module}/../Lambda_src/result_processor/app.py"
  output_path = "${path.module}/.terraform/result_processor.zip"
}

resource "aws_lambda_function" "result_processor" {
  function_name = "${var.project_name}-${var.environment}-result-processor"
  role          = aws_iam_role.result_processor.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  timeout       = 300
  memory_size   = 512

  filename         = data.archive_file.result_processor.output_path
  source_code_hash = data.archive_file.result_processor.output_base64sha256

  dead_letter_config {
    target_arn = aws_sqs_queue.result_processor_dlq.arn
  }

  environment {
    variables = {
      DEEPSEEK_API_KEY_SECRET_ARN = aws_secretsmanager_secret.deepseek_api_key.arn
      DEEPSEEK_API_URL            = var.deepseek_api_url
      DEEPSEEK_MODEL              = var.deepseek_model
      DOCUMENTS_BUCKET            = aws_s3_bucket.documents.id
      DOCUMENTS_TABLE             = aws_dynamodb_table.documents.name
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-result-processor"
  }
}

resource "aws_lambda_permission" "sns_result_processor" {
  statement_id  = "AllowSnsInvokeResultProcessor"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.result_processor.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.textract_results.arn
}
