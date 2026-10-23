terraform {
  backend "s3" {
    bucket               = "getddo-tfstate-942466928325-ap-northeast-2"
    key                  = "staging/terraform.tfstate"
    region               = "ap-northeast-2"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "staging/workspaces"
  }
}
