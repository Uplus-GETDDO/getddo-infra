# 앱 연결과 관리 접속의 전체 한도를 재생성 가능한 설정으로 관리한다.
resource "aws_db_parameter_group" "stage" {
  name        = "getddo-staging-mysql84"
  family      = "mysql8.4"
  description = "MySQL connection capacity for staging applications"

  parameter {
    name         = "max_connections"
    value        = "100"
    apply_method = "immediate"
  }

  tags = { Name = "getddo-staging-mysql84" }
}

resource "aws_db_subnet_group" "stage" {
  name       = "getddo-staging-db"
  subnet_ids = [for s in data.aws_subnet.data : s.id]
  tags       = { Name = "getddo-staging-db" }
}
resource "aws_db_instance" "stage" {
  identifier                 = "getddo-staging-mysql"
  engine                     = "mysql"
  engine_version             = "8.4.11"
  parameter_group_name       = aws_db_parameter_group.stage.name
  instance_class             = "db.t4g.medium"
  db_name                    = "getddo"
  username                   = "getddoadmin"
  password_wo                = var.db_master_password
  password_wo_version        = 1
  allocated_storage          = 20
  storage_type               = "gp3"
  storage_encrypted          = true
  multi_az                   = false
  apply_immediately          = var.db_apply_immediately
  publicly_accessible        = false
  db_subnet_group_name       = aws_db_subnet_group.stage.name
  vpc_security_group_ids     = [aws_security_group.stage["db"].id]
  backup_retention_period    = 0
  auto_minor_version_upgrade = false
  engine_lifecycle_support   = "open-source-rds-extended-support-disabled"
  deletion_protection        = false
  skip_final_snapshot        = true
  tags                       = { Name = "getddo-staging-mysql" }
}
resource "aws_elasticache_subnet_group" "stage" {
  name       = "getddo-staging-redis"
  subnet_ids = [for s in data.aws_subnet.data : s.id]
  tags       = { Name = "getddo-staging-redis" }
}
resource "aws_elasticache_replication_group" "stage" {
  replication_group_id       = "getddo-staging-redis"
  description                = "Dedicated temporary staging Redis"
  engine                     = "redis"
  engine_version             = "7.1"
  parameter_group_name       = "default.redis7"
  node_type                  = "cache.t4g.micro"
  port                       = 6379
  cluster_mode               = "disabled"
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false
  subnet_group_name          = aws_elasticache_subnet_group.stage.name
  security_group_ids         = [aws_security_group.stage["redis"].id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token_wo              = var.redis_auth_token
  auth_token_wo_version      = 1
  auth_token_update_strategy = "ROTATE"
  snapshot_retention_limit   = 0
  auto_minor_version_upgrade = false
  tags                       = { Name = "getddo-staging-redis" }
}
