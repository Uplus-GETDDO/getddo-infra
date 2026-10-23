terraform {
  required_version = "~> 1.16.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket               = "getddo-tfstate-942466928325-ap-northeast-2"
    key                  = "staging-control/terraform.tfstate"
    region               = "ap-northeast-2"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "staging-control/workspaces"
  }
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = ["942466928325"]
  default_tags { tags = { Environment = "staging", Lifecycle = "persistent-control" } }
}
