variable "aws_region" {
  description = "AWS region for bootstrap resources and the deployment pipeline."
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

variable "github_repository" {
  description = "GitHub repository allowed to assume the deployment role (owner/repo)."
  type        = string
  default     = "amitvg1997/Aethon"
}
