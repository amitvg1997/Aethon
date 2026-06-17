resource "aws_apigatewayv2_api" "uploads" {
  name          = "${var.project_name}-${var.environment}-uploads"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = local.cors_allowed_origins
    allow_methods = ["POST", "OPTIONS"]
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
}

resource "aws_apigatewayv2_route" "query" {
  api_id = aws_apigatewayv2_api.uploads.id

  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.query.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.uploads.id

  name        = "$default"
  auto_deploy = true
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
