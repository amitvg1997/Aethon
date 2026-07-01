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
      "s3vectors:CreateIndex",
      "s3vectors:DeleteIndex",
      "s3vectors:DescribeIndex",
      "s3vectors:PutVectors",
      "s3vectors:GetVectors",
      "s3vectors:DeleteVectors",
      "s3vectors:QueryVectors",
      "s3vectors:ListVectors",
    ]

    resources = [
      aws_s3vectors_index.knowledge_base.index_arn,
    ]
  }

  statement {
    effect = "Allow"

    actions = [
      "s3vectors:DescribeVectorBucket",
    ]

    resources = [
      aws_s3vectors_vector_bucket.knowledge_base.vector_bucket_arn,
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
    type = "S3_VECTORS"

    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.knowledge_base.index_arn
    }
  }

  depends_on = [
    aws_iam_role_policy.bedrock_knowledge_base,
    aws_s3vectors_index.knowledge_base,
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

  # knowledge_base_id isn't treated as ForceNew by the provider, but Bedrock
  # data sources can't be moved between knowledge bases — an in-place update
  # 404s. Force a replace whenever the parent KB is replaced (e.g. the
  # vector index config changes) instead of attempting an impossible update.
  lifecycle {
    replace_triggered_by = [aws_bedrockagent_knowledge_base.documents.id]
  }
}
