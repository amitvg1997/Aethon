resource "aws_apigatewayv2_api" "uploads" {
  name          = "${var.project_name}-${var.environment}-uploads"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = local.cors_allowed_origins
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3000
  }
}

resource "aws_apigatewayv2_integration" "upload_url" {
  api_id = aws_apigatewayv2_api.uploads.id

  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.upload_url.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "create_upload" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "POST /uploads"
  target    = "integrations/${aws_apigatewayv2_integration.upload_url.id}"
}

resource "aws_apigatewayv2_integration" "query" {
  api_id = aws_apigatewayv2_api.uploads.id

  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query.invoke_arn
  payload_format_version = "2.0"

  # HTTP APIs hard-cap integration timeouts at 30000ms. The query Lambda
  # (timeout 28s) must finish inside this window, otherwise API Gateway
  # returns an opaque 504 to the client while the Lambda keeps running.
  timeout_milliseconds = 29000
}

resource "aws_apigatewayv2_route" "query" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_integration" "kb_sync" {
  api_id = aws_apigatewayv2_api.uploads.id

  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.kb_sync.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "start_kb_sync" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "POST /sync"
  target    = "integrations/${aws_apigatewayv2_integration.kb_sync.id}"
}

resource "aws_apigatewayv2_route" "get_kb_sync_status" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "GET /sync"
  target    = "integrations/${aws_apigatewayv2_integration.kb_sync.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.uploads.id

  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "eval" {
  api_id = aws_apigatewayv2_api.uploads.id

  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.eval.invoke_arn
  payload_format_version = "2.0"

  # See the note on the query integration: HTTP APIs hard-cap integration
  # timeouts at 30000ms, and the eval Lambda (timeout 29s) runs a filtered
  # and unfiltered retrieval+answer pipeline concurrently to fit under it.
  timeout_milliseconds = 30000
}

resource "aws_apigatewayv2_route" "run_eval" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "POST /eval"
  target    = "integrations/${aws_apigatewayv2_integration.eval.id}"
}

resource "aws_apigatewayv2_route" "list_eval" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "GET /eval"
  target    = "integrations/${aws_apigatewayv2_integration.eval.id}"
}

resource "aws_apigatewayv2_route" "clear_eval" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "DELETE /eval"
  target    = "integrations/${aws_apigatewayv2_integration.eval.id}"
}

resource "aws_lambda_permission" "api_gateway_upload_url" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_url.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.uploads.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gateway_query" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.uploads.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gateway_kb_sync" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.kb_sync.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.uploads.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gateway_eval" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.eval.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.uploads.execution_arn}/*/*"
}
