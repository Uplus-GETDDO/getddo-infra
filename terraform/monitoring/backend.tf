terraform {
  backend "s3" {
    bucket       = "getddo-tfstate-942466928325-ap-northeast-2"
    key          = "monitoring/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
