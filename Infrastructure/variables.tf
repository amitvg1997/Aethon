variable "aws_region" {
  description = "AWS region where the Phase 1 MVP resources will be deployed."
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "Project name used as a prefix for AWS resource names."
  type        = string
  default     = "aethon"
}

variable "environment" {
  description = "Deployment environment, such as dev, staging, or prod."
  type        = string
  default     = "dev"
}

variable "allowed_cors_origins" {
  description = "Frontend origins allowed to call the API and upload documents."
  type        = list(string)
  default     = ["http://localhost:3000"]
}

variable "deepseek_api_url" {
  description = "OpenAI-compatible chat completions endpoint used for metadata extraction."
  type        = string
  default     = "https://api.groq.com/openai/v1/chat/completions"
}

variable "deepseek_model" {
  description = "Chat model used for document metadata extraction."
  type        = string
  default     = "llama-3.3-70b-versatile"
}

variable "embedding_model_id" {
  description = "Amazon Bedrock embedding model used by the Knowledge Base."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "knowledge_base_chunk_max_tokens" {
  description = "Maximum number of tokens in each fixed-size Knowledge Base chunk."
  type        = number
  default     = 500
}

variable "knowledge_base_chunk_overlap_percentage" {
  description = "Percentage overlap between adjacent fixed-size Knowledge Base chunks."
  type        = number
  default     = 20
}

