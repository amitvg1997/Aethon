resource "aws_sns_topic" "textract_results" {
  name = "${var.project_name}-${var.environment}-textract-results"

  tags = {
    Name = "${var.project_name}-${var.environment}-textract-results"
  }
}

data "aws_iam_policy_document" "textract_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["textract.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "textract_sns_publish" {
  name               = "${var.project_name}-${var.environment}-textract-sns-publish"
  assume_role_policy = data.aws_iam_policy_document.textract_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-textract-sns-publish"
  }
}

data "aws_iam_policy_document" "textract_sns_publish" {
  statement {
    effect = "Allow"

    actions = [
      "sns:Publish",
    ]

    resources = [
      aws_sns_topic.textract_results.arn,
    ]
  }
}

resource "aws_iam_role_policy" "textract_sns_publish" {
  name   = "${var.project_name}-${var.environment}-textract-sns-publish"
  role   = aws_iam_role.textract_sns_publish.id
  policy = data.aws_iam_policy_document.textract_sns_publish.json
}

resource "aws_sns_topic_subscription" "textract_results_to_result_processor" {
  topic_arn = aws_sns_topic.textract_results.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.result_processor.arn

  depends_on = [
    aws_lambda_permission.sns_result_processor,
  ]
}
