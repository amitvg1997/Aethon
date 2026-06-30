resource "aws_secretsmanager_secret" "deepseek_api_key" {
  name                    = "${var.project_name}/${var.environment}/deepseek-api-key"
  description             = "DeepSeek API key used by the result_processor Lambda."
  recovery_window_in_days = 0

  tags = {
    Name = "${var.project_name}-${var.environment}-deepseek-api-key"
  }
}
