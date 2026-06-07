output "terraform_state_bucket" {
  description = "S3 bucket that stores Terraform remote state for the main Infrastructure stack."
  value       = aws_s3_bucket.terraform_state.id
}

output "terraform_lock_table" {
  description = "DynamoDB table used for Terraform state locking."
  value       = aws_dynamodb_table.terraform_lock.name
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions. Add this as the AWS_ROLE_ARN repository secret."
  value       = aws_iam_role.github_actions.arn
}

output "aws_region" {
  description = "AWS region used by the deployment pipeline."
  value       = var.aws_region
}

output "backend_config" {
  description = "Copy these values into Infrastructure/backend.hcl after bootstrap completes."
  value = <<-EOT
    bucket         = "${aws_s3_bucket.terraform_state.id}"
    key            = "infrastructure/terraform.tfstate"
    region         = "${var.aws_region}"
    dynamodb_table = "${aws_dynamodb_table.terraform_lock.name}"
    encrypt        = true
  EOT
}
