output "documents_bucket_name" {
  description = "Name of the private S3 bucket used for document uploads."
  value       = aws_s3_bucket.documents.id
}

output "uploads_api_endpoint" {
  description = "Base URL for the document upload API. Request a presigned URL with POST /uploads."
  value       = aws_apigatewayv2_api.uploads.api_endpoint
}
