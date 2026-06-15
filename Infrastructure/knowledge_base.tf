data "aws_iam_policy_document" "bedrock_knowledge_base_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "bedrock_knowledge_base" {
  name               = "${var.project_name}-${var.environment}-bedrock-kb"
  assume_role_policy = data.aws_iam_policy_document.bedrock_knowledge_base_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-bedrock-kb"
  }
}

data "aws_iam_policy_document" "bedrock_knowledge_base" {
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
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.documents.arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
    ]

    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.embedding_model_id}",
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "aoss:APIAccessAll",
    ]

    resources = [
      aws_opensearchserverless_collection.knowledge_base.arn,
    ]
  }
}

resource "aws_iam_role_policy" "bedrock_knowledge_base" {
  name   = "${var.project_name}-${var.environment}-bedrock-kb"
  role   = aws_iam_role.bedrock_knowledge_base.id
  policy = data.aws_iam_policy_document.bedrock_knowledge_base.json
}

resource "aws_bedrockagent_knowledge_base" "documents" {
  name     = local.knowledge_base_name
  role_arn = aws_iam_role.bedrock_knowledge_base.arn

  knowledge_base_configuration {
    type = "VECTOR"

    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.embedding_model_id}"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"

    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.knowledge_base.arn
      vector_index_name = opensearch_index.knowledge_base.name

      field_mapping {
        metadata_field = local.opensearch_metadata_field_name
        text_field     = local.opensearch_text_field_name
        vector_field   = local.opensearch_vector_field_name
      }
    }
  }

  depends_on = [
    aws_iam_role_policy.bedrock_knowledge_base,
    aws_opensearchserverless_access_policy.knowledge_base,
    opensearch_index.knowledge_base,
  ]

  tags = {
    Name = local.knowledge_base_name
  }
}

resource "aws_bedrockagent_data_source" "documents" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.documents.id
  name              = "${var.project_name}-${var.environment}-documents"

  data_source_configuration {
    type = "S3"

    s3_configuration {
      bucket_arn         = aws_s3_bucket.documents.arn
      inclusion_prefixes = ["uploads/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"

      fixed_size_chunking_configuration {
        max_tokens         = var.knowledge_base_chunk_max_tokens
        overlap_percentage = var.knowledge_base_chunk_overlap_percentage
      }
    }
  }
}
