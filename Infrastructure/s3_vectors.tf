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

  # Bedrock stores each chunk's full text (AMAZON_BEDROCK_TEXT) and source
  # location (AMAZON_BEDROCK_METADATA) as vector metadata. S3 Vectors caps
  # *filterable* metadata at 2048 bytes/record; without excluding these two
  # keys, a single chunk near the configured max_tokens size overflows that
  # cap and the whole KB data source sync fails.
  metadata_configuration {
    non_filterable_metadata_keys = [
      "AMAZON_BEDROCK_TEXT",
      "AMAZON_BEDROCK_METADATA",
    ]
  }
}
