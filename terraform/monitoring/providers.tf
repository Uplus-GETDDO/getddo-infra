terraform {
  required_version = "~> 1.16.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = ["942466928325"]
  default_tags {
    tags = { Environment = "prod", Service = "monitoring", ManagedBy = "terraform" }
  }
}
