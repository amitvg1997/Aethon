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
