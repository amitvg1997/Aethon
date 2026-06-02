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
