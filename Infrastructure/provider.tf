provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

provider "opensearch" {
  url         = aws_opensearchserverless_collection.knowledge_base.collection_endpoint
  healthcheck = false
  aws_region  = var.aws_region
}
