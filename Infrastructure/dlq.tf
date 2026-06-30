# Catch-all for invocation failures that per-record error handling in the
# Lambda code can't catch (e.g. the function times out or is OOM-killed
# mid-batch). Without these, S3/SNS async invocations would exhaust their
# retries and silently drop the event, leaving a document stuck forever.
resource "aws_sqs_queue" "memory_optimizer_dlq" {
  name                      = "${var.project_name}-${var.environment}-memory-optimizer-dlq"
  message_retention_seconds = 1209600 # 14 days

  tags = {
    Name = "${var.project_name}-${var.environment}-memory-optimizer-dlq"
  }
}

resource "aws_sqs_queue" "result_processor_dlq" {
  name                      = "${var.project_name}-${var.environment}-result-processor-dlq"
  message_retention_seconds = 1209600 # 14 days

  tags = {
    Name = "${var.project_name}-${var.environment}-result-processor-dlq"
  }
}
