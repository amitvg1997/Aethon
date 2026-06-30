terraform {
  required_version = ">= 1.6.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.90"
    }

    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }


  }
}
