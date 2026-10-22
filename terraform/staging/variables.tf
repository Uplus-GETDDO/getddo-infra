variable "run_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9-]{8,48}$", var.run_id))
    error_message = "A staging run identifier is required."
  }
}
variable "ami_id" { type = string }
variable "monitoring_security_group_id" { type = string }
variable "shared_alb_security_group_id" { type = string }
variable "db_master_password" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "redis_auth_token" {
  type      = string
  sensitive = true
  ephemeral = true
}
