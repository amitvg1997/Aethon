data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "upload_url" {
  name               = "${var.project_name}-${var.environment}-upload-url"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-upload-url"
  }
}

resource "aws_iam_role_policy_attachment" "upload_url_logs" {
  role       = aws_iam_role.upload_url.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "upload_url_s3" {
  statement {
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.documents.arn}/uploads/*",
    ]
  }
}

resource "aws_iam_role_policy" "upload_url_s3" {
  name   = "${var.project_name}-${var.environment}-upload-url-s3"
  role   = aws_iam_role.upload_url.id
  policy = data.aws_iam_policy_document.upload_url_s3.json
}

resource "aws_iam_role" "memory_optimizer" {
  name               = "${var.project_name}-${var.environment}-memory-optimizer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-memory-optimizer"
  }
}

resource "aws_iam_role_policy_attachment" "memory_optimizer_logs" {
  role       = aws_iam_role.memory_optimizer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "memory_optimizer" {
  statement {
    effect = "Allow"

    actions = [
      "s3:GetObject",
    ]

    resources = [
      "${aws_s3_bucket.documents.arn}/uploads/*",
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]

    resources = [
      aws_dynamodb_table.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "textract:StartDocumentTextDetection",
    ]

    resources = ["*"]
  }

  statement {
    effect = "Allow"

    actions = [
      "iam:PassRole",
    ]

    resources = [
      aws_iam_role.textract_sns_publish.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "sqs:SendMessage",
    ]

    resources = [
      aws_sqs_queue.memory_optimizer_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "memory_optimizer" {
  name   = "${var.project_name}-${var.environment}-memory-optimizer"
  role   = aws_iam_role.memory_optimizer.id
  policy = data.aws_iam_policy_document.memory_optimizer.json
}

resource "aws_iam_role" "result_processor" {
  name               = "${var.project_name}-${var.environment}-result-processor"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-result-processor"
  }
}

resource "aws_iam_role_policy_attachment" "result_processor_logs" {
  role       = aws_iam_role.result_processor.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "result_processor" {
  statement {
    effect = "Allow"

    actions = [
      "textract:GetDocumentTextDetection",
    ]

    resources = ["*"]
  }

  statement {
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = [
      "${aws_s3_bucket.documents.arn}/extracted-text/*",
      "${aws_s3_bucket.documents.arn}/uploads/*",
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "dynamodb:UpdateItem",
    ]

    resources = [
      aws_dynamodb_table.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.deepseek_api_key.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "bedrock:StartIngestionJob",
      "bedrock:GetIngestionJob",
    ]

    resources = ["*"]
  }

  statement {
    effect = "Allow"

    actions = [
      "sqs:SendMessage",
    ]

    resources = [
      aws_sqs_queue.result_processor_dlq.arn,
    ]
  }
}

resource "aws_iam_role_policy" "result_processor" {
  name   = "${var.project_name}-${var.environment}-result-processor"
  role   = aws_iam_role.result_processor.id
  policy = data.aws_iam_policy_document.result_processor.json
}

resource "aws_iam_role" "query" {
  name               = "${var.project_name}-${var.environment}-query"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-query"
  }
}

resource "aws_iam_role_policy_attachment" "query_logs" {
  role       = aws_iam_role.query.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "query" {
  statement {
    effect = "Allow"

    actions = [
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "bedrock:Retrieve",
    ]

    resources = [
      aws_bedrockagent_knowledge_base.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.deepseek_api_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "query" {
  name   = "${var.project_name}-${var.environment}-query"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query.json
}

resource "aws_iam_role" "eval" {
  name               = "${var.project_name}-${var.environment}-eval"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-eval"
  }
}

resource "aws_iam_role_policy_attachment" "eval_logs" {
  role       = aws_iam_role.eval.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "eval" {
  statement {
    effect = "Allow"

    actions = [
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.eval_results.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "bedrock:Retrieve",
    ]

    resources = [
      aws_bedrockagent_knowledge_base.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue",
    ]

    resources = [
      aws_secretsmanager_secret.deepseek_api_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "eval" {
  name   = "${var.project_name}-${var.environment}-eval"
  role   = aws_iam_role.eval.id
  policy = data.aws_iam_policy_document.eval.json
}
