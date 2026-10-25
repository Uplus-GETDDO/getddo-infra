terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "aws_region" {
  description = "AWS region for the production service"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "현재 네트워크와 AMI 구성은 서울 리전(ap-northeast-2) 전용입니다."
  }
}

provider "aws" {
  region = var.aws_region
  # Preserve the C preparation root account guard after consolidation.
  allowed_account_ids = ["942466928325"]
}

data "aws_caller_identity" "current" {}

output "aws_account_id" {
  description = "AWS account used by Terraform"
  value       = data.aws_caller_identity.current.account_id
}

output "aws_caller_arn" {
  description = "AWS identity used by Terraform"
  value       = data.aws_caller_identity.current.arn
}

provider "aws" {
  alias               = "edge"
  region              = "us-east-1"
  allowed_account_ids = ["942466928325"]
}
