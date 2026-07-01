resource "aws_dynamodb_table" "documents" {
  name         = "${var.project_name}-${var.environment}-documents"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document_id"

  attribute {
    name = "document_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-documents"
  }
}

resource "aws_dynamodb_table" "eval_results" {
  name         = "${var.project_name}-${var.environment}-eval-results"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "eval_id"

  attribute {
    name = "eval_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-eval-results"
  }
}
