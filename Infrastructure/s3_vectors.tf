resource "aws_s3vectors_vector_bucket" "knowledge_base" {
  vector_bucket_name = "${var.project_name}-${var.environment}-vectors"

  tags = {
    Name = "${var.project_name}-${var.environment}-vectors"
  }
}

resource "aws_s3vectors_index" "knowledge_base" {
  vector_bucket_name = aws_s3vectors_vector_bucket.knowledge_base.vector_bucket_name
  index_name         = "${var.project_name}-${var.environment}-kb-index"

  data_type       = "float32"
  dimension       = local.titan_embedding_vector_dimensions
  distance_metric = "euclidean"
}
