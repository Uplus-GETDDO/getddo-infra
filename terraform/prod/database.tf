# 앱 연결과 관리 접속의 전체 한도를 재생성 가능한 설정으로 관리한다.
resource "aws_db_parameter_group" "main" {
  name        = "getddo-dev-mysql84"
  family      = "mysql8.4"
  description = "MySQL connection capacity for prod applications"

  parameter {
    name         = "max_connections"
    value        = "100"
    apply_method = "immediate"
  }

  tags = { Name = "getddo-dev-mysql84" }
}

resource "aws_db_subnet_group" "main" {
  name       = "getddo-dev-db"
  subnet_ids = [for subnet in aws_subnet.data : subnet.id]
  tags       = { Name = "getddo-dev-db-subnets" }
}

resource "aws_db_instance" "main" {
  identifier           = "getddo-dev-mysql"
  engine               = "mysql"
  engine_version       = "8.4"
  parameter_group_name = aws_db_parameter_group.main.name
  instance_class       = "db.t4g.medium"

  db_name             = "getddo"
  username            = "getddoadmin"
  password_wo         = var.db_master_password
  password_wo_version = var.db_master_password_version

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  # 기존 DNS를 유지하는 동기 대기 DB다. 대기 DB는 읽기 트래픽을 받지 않는다.
  multi_az = true
  # 승인된 전환 plan에서만 즉시 적용을 선택하고 평소에는 유지보수 창을 따른다.
  apply_immediately      = var.db_apply_immediately
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period      = 1
  auto_minor_version_upgrade   = true
  allow_major_version_upgrade  = false
  engine_lifecycle_support     = "open-source-rds-extended-support-disabled"
  performance_insights_enabled = false
  monitoring_interval          = 0

  # 검증 데이터를 실수로 삭제하지 않도록 보호한다. 정리할 때 별도 변경·검토한다.
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "getddo-dev-mysql-final"
  copy_tags_to_snapshot     = true

  tags = { Name = "getddo-dev-mysql" }

  depends_on = [aws_route_table_association.data]
}
