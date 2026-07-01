output "documents_bucket_name" {
  description = "Name of the private S3 bucket used for document uploads."
  value       = aws_s3_bucket.documents.id
}

output "uploads_api_endpoint" {
  description = "Base URL for the document API. Request a presigned URL with POST /uploads and query documents with POST /query."
  value       = aws_apigatewayv2_api.uploads.api_endpoint
}

output "frontend_bucket_name" {
  description = "Name of the public S3 bucket used to host the static frontend."
  value       = aws_s3_bucket.frontend.id
}

output "frontend_website_endpoint" {
  description = "S3 website endpoint for the static frontend."
  value       = aws_s3_bucket_website_configuration.frontend.website_endpoint
}

output "frontend_url" {
  description = "HTTP URL for the static frontend."
  value       = "http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}"
}

output "memory_optimizer_function_name" {
  description = "Name of the Lambda function triggered after document uploads."
  value       = aws_lambda_function.memory_optimizer.function_name
}

output "result_processor_function_name" {
  description = "Name of the Lambda function triggered after Textract finishes processing a document."
  value       = aws_lambda_function.result_processor.function_name
}

output "query_function_name" {
  description = "Name of the Lambda function used by the document Q&A API."
  value       = aws_lambda_function.query.function_name
}

output "eval_function_name" {
  description = "Name of the Lambda function used by the filtered-vs-unfiltered evaluation API."
  value       = aws_lambda_function.eval.function_name
}

output "textract_results_topic_arn" {
  description = "SNS topic ARN where Textract publishes async job completion events."
  value       = aws_sns_topic.textract_results.arn
}

output "deepseek_api_key_secret_name" {
  description = "Secrets Manager secret name where the DeepSeek API key must be stored."
  value       = aws_secretsmanager_secret.deepseek_api_key.name
}

output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID used for document ingestion."
  value       = aws_bedrockagent_knowledge_base.documents.id
}

output "knowledge_base_data_source_id" {
  description = "Bedrock Knowledge Base S3 data source ID for uploaded documents."
  value       = aws_bedrockagent_data_source.documents.data_source_id
}

output "s3_vectors_bucket_name" {
  description = "S3 Vectors vector bucket backing the Knowledge Base."
  value       = aws_s3vectors_vector_bucket.knowledge_base.vector_bucket_name
}

output "s3_vectors_index_name" {
  description = "S3 Vectors index used by the Knowledge Base."
  value       = aws_s3vectors_index.knowledge_base.index_name
}
