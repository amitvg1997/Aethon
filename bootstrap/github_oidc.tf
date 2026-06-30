data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com",
  ]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-github-actions-oidc"
  }
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.project_name}-${var.environment}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json

  tags = {
    Name = "${var.project_name}-${var.environment}-github-actions"
  }
}

data "aws_iam_policy_document" "github_actions_deploy" {
  statement {
    sid    = "TerraformState"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.terraform_state.arn,
      "${aws_s3_bucket.terraform_state.arn}/*",
    ]
  }

  statement {
    sid    = "TerraformLock"
    effect = "Allow"

    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]

    resources = [
      aws_dynamodb_table.terraform_lock.arn,
    ]
  }

  statement {
    sid    = "S3ProjectBuckets"
    effect = "Allow"

    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketCors",
      "s3:DeleteBucketEncryption",
      "s3:DeleteBucketPolicy",
      "s3:DeleteBucketTagging",
      "s3:DeleteBucketWebsite",
      "s3:DeletePublicAccessBlock",
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketAcl",
      "s3:GetBucketLocation",
      "s3:GetBucketLogging",
      "s3:GetBucketNotification",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketPolicy",
      "s3:GetBucketPolicyStatus",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketCors",
      "s3:GetBucketNotification",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketVersioning",
      "s3:GetBucketWebsite",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutBucketCors",
      "s3:PutBucketNotification",
      "s3:PutBucketVersioning",
      "s3:PutBucketWebsite",
      "s3:PutEncryptionConfiguration",
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:PutBucketTagging",
      "s3:GetBucketTagging",
    ]

    resources = [
      "arn:aws:s3:::${var.project_name}-${var.environment}-documents-*",
      "arn:aws:s3:::${var.project_name}-${var.environment}-documents-*/*",
      "arn:aws:s3:::${var.project_name}-${var.environment}-frontend-*",
      "arn:aws:s3:::${var.project_name}-${var.environment}-frontend-*/*",
    ]
  }

  statement {
    sid    = "DynamoDBDocuments"
    effect = "Allow"

    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:ListTagsOfResource",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTimeToLive",
    ]

    resources = [
      "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.project_name}-${var.environment}-documents",
    ]
  }

  statement {
    sid    = "Lambda"
    effect = "Allow"

    actions = [
      "lambda:AddPermission",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConfiguration",
      "lambda:GetPolicy",
      "lambda:GetRuntimeManagementConfig",
      "lambda:ListVersionsByFunction",
      "lambda:ListTags",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]

    resources = [
      "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-${var.environment}-*",
    ]
  }

  statement {
    sid    = "Sns"
    effect = "Allow"

    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:GetSubscriptionAttributes",
      "sns:ListSubscriptionsByTopic",
      "sns:ListTagsForResource",
      "sns:SetTopicAttributes",
      "sns:SetSubscriptionAttributes",
      "sns:Subscribe",
      "sns:TagResource",
      "sns:Unsubscribe",
      "sns:UntagResource",
    ]

    resources = [
      "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.project_name}-${var.environment}-*",
    ]
  }

  statement {
    sid    = "SecretsManager"
    effect = "Allow"

    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:ListSecretVersionIds",
      "secretsmanager:ListSecrets",
      "secretsmanager:PutResourcePolicy",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:UpdateSecret",
    ]

    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.project_name}/${var.environment}/deepseek-api-key-*",
    ]
  }

  statement {
    sid    = "S3Vectors"
    effect = "Allow"

    actions = [
      "s3vectors:CreateVectorBucket",
      "s3vectors:DeleteVectorBucket",
      "s3vectors:GetVectorBucket",
      "s3vectors:ListVectorBuckets",
      "s3vectors:PutVectorBucketPolicy",
      "s3vectors:GetVectorBucketPolicy",
      "s3vectors:DeleteVectorBucketPolicy",
      "s3vectors:CreateIndex",
      "s3vectors:DeleteIndex",
      "s3vectors:GetIndex",
      "s3vectors:ListIndexes",
      "s3vectors:PutVectors",
      "s3vectors:GetVectors",
      "s3vectors:DeleteVectors",
      "s3vectors:QueryVectors",
      "s3vectors:ListVectors",
      "s3vectors:ListTagsForResource",
      "s3vectors:TagResource",
      "s3vectors:UntagResource",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "BedrockKnowledgeBase"
    effect = "Allow"

    actions = [
      "bedrock:CreateDataSource",
      "bedrock:CreateKnowledgeBase",
      "bedrock:DeleteDataSource",
      "bedrock:DeleteKnowledgeBase",
      "bedrock:GetDataSource",
      "bedrock:GetIngestionJob",
      "bedrock:GetKnowledgeBase",
      "bedrock:InvokeModel",
      "bedrock:ListDataSources",
      "bedrock:ListIngestionJobs",
      "bedrock:ListKnowledgeBases",
      "bedrock:ListTagsForResource",
      "bedrock:StartIngestionJob",
      "bedrock:TagResource",
      "bedrock:UntagResource",
      "bedrock:UpdateDataSource",
      "bedrock:UpdateKnowledgeBase",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "ApiGateway"
    effect = "Allow"

    actions = [
      "apigateway:GET",
      "apigateway:POST",
      "apigateway:PUT",
      "apigateway:PATCH",
      "apigateway:DELETE",
      "apigateway:TagResource",
      "apigateway:UntagResource",
    ]

    resources = [
      "arn:aws:apigateway:${var.aws_region}::/apis",
      "arn:aws:apigateway:${var.aws_region}::/apis/*",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/integrations",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/integrations/*",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/routes",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/routes/*",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/stages",
      "arn:aws:apigateway:${var.aws_region}::/apis/*/stages/*",
      "arn:aws:apigateway:${var.aws_region}::/v2/apis",
      "arn:aws:apigateway:${var.aws_region}::/v2/apis/*",
      "arn:aws:apigateway:${var.aws_region}::/tags/*",
    ]
  }

  statement {
    sid    = "IamProjectRoles"
    effect = "Allow"

    actions = [
      "iam:AttachRolePolicy",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]

    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.environment}-*",
    ]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:ListTagsForResource",
      "logs:PutLogEvents",
      "logs:TagResource",
      "logs:UntagResource",
    ]

    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-*",
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-*:*",
    ]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "${var.project_name}-${var.environment}-github-actions-deploy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}
