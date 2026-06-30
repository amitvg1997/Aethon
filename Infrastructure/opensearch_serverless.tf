data "aws_caller_identity" "current" {}

locals {
  knowledge_base_name               = "${var.project_name}-${var.environment}-knowledge-base"
  titan_embedding_vector_dimensions = 1024
}
