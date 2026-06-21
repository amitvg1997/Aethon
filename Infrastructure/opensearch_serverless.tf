data "aws_caller_identity" "current" {}

locals {
  knowledge_base_name               = "${var.project_name}-${var.environment}-knowledge-base"
  opensearch_collection_name        = "${var.project_name}-${var.environment}-kb"
  opensearch_vector_index_name      = "${var.project_name}-${var.environment}-kb-index"
  opensearch_vector_field_name      = "aethon-vector"
  opensearch_text_field_name        = "aethon-text"
  opensearch_metadata_field_name    = "aethon-metadata"
  opensearch_admin_principal_arns   = distinct(concat([data.aws_caller_identity.current.arn], var.opensearch_index_admin_principal_arns))
  opensearch_access_principal_arns  = distinct(concat([aws_iam_role.bedrock_knowledge_base.arn], local.opensearch_admin_principal_arns))
  titan_embedding_vector_dimensions = 1024
}

resource "aws_opensearchserverless_security_policy" "knowledge_base_encryption" {
  name = "${var.project_name}-${var.environment}-kb-enc"
  type = "encryption"

  policy = jsonencode({
    Rules = [
      {
        Resource = [
          "collection/${local.opensearch_collection_name}",
        ]
        ResourceType = "collection"
      }
    ]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "knowledge_base_network" {
  name = "${var.project_name}-${var.environment}-kb-net"
  type = "network"

  policy = jsonencode([
    {
      Rules = [
        {
          Resource = [
            "collection/${local.opensearch_collection_name}",
          ]
          ResourceType = "collection"
        },
        {
          Resource = [
            "collection/${local.opensearch_collection_name}",
          ]
          ResourceType = "dashboard"
        }
      ]
      AllowFromPublic = true
    }
  ])
}

resource "aws_opensearchserverless_access_policy" "knowledge_base" {
  name = "${var.project_name}-${var.environment}-kb"
  type = "data"

  policy = jsonencode([
    {
      Rules = [
        {
          Resource = [
            "collection/${local.opensearch_collection_name}",
          ]
          Permission = [
            "aoss:CreateCollectionItems",
            "aoss:DeleteCollectionItems",
            "aoss:DescribeCollectionItems",
            "aoss:UpdateCollectionItems",
          ]
          ResourceType = "collection"
        },
        {
          Resource = [
            "index/${local.opensearch_collection_name}/*",
          ]
          Permission = [
            "aoss:CreateIndex",
            "aoss:DeleteIndex",
            "aoss:DescribeIndex",
            "aoss:ReadDocument",
            "aoss:UpdateIndex",
            "aoss:WriteDocument",
          ]
          ResourceType = "index"
        }
      ]
      Principal = local.opensearch_access_principal_arns
    }
  ])
}

resource "aws_opensearchserverless_collection" "knowledge_base" {
  name = local.opensearch_collection_name
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_security_policy.knowledge_base_encryption,
    aws_opensearchserverless_security_policy.knowledge_base_network,
  ]

  tags = {
    Name = local.opensearch_collection_name
  }
}

resource "opensearch_index" "knowledge_base" {
  name      = local.opensearch_vector_index_name
  index_knn = true

  mappings = jsonencode({
    properties = {
      (local.opensearch_vector_field_name) = {
        type      = "knn_vector"
        dimension = local.titan_embedding_vector_dimensions
        method = {
          name       = "hnsw"
          engine     = "faiss"
          space_type = "l2"
        }
      }
      (local.opensearch_text_field_name) = {
        type = "text"
      }
      (local.opensearch_metadata_field_name) = {
        type  = "text"
        index = false
      }
    }
  })

  depends_on = [
    aws_opensearchserverless_access_policy.knowledge_base,
    aws_opensearchserverless_collection.knowledge_base,
  ]

  # Bedrock extends the index mapping as it ingests documents. Preserve those
  # managed fields instead of replacing an index that already contains vectors.
  lifecycle {
    ignore_changes = [mappings]
  }
}
