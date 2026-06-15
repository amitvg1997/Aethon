resource "aws_s3_bucket" "documents" {
  bucket_prefix = "${var.project_name}-${var.environment}-documents-"

  tags = {
    Name = "${var.project_name}-${var.environment}-documents"
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  cors_rule {
    allowed_origins = var.allowed_cors_origins
    allowed_methods = ["PUT"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_lambda_permission" "s3_memory_optimizer" {
  statement_id  = "AllowS3InvokeMemoryOptimizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.memory_optimizer.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}

resource "aws_s3_bucket_notification" "documents" {
  bucket = aws_s3_bucket.documents.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.memory_optimizer.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [
    aws_lambda_permission.s3_memory_optimizer,
  ]
}
