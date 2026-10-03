# AetherAI Terraform (spec Section 39).
#
# HONEST SCOPE NOTE: this is a syntactically valid, sensibly structured
# skeleton for deploying AetherAI to AWS (ECR + ECS Fargate + RDS Postgres
# + ALB), written to `terraform validate` cleanly. It was NOT run against
# a real AWS account in the environment this project was built in (no
# cloud credentials/egress available there), so treat it as a documented
# starting point, not a deployment that's been proven end-to-end. Review
# instance sizes, IAM policies, and network CIDRs before applying to a
# real account.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------- network --
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = "aetherai-${var.environment}" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = { Name = "aetherai-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = { Name = "aetherai-private-${count.index}" }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --------------------------------------------------------- security groups --
resource "aws_security_group" "backend" {
  name_prefix = "aetherai-backend-"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name_prefix = "aetherai-db-"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }
}

resource "aws_security_group" "alb" {
  name_prefix = "aetherai-alb-"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# -------------------------------------------------------------- database ---
resource "aws_db_subnet_group" "main" {
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier             = "aetherai-${var.environment}"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = 20
  db_name                = "aetherai"
  username               = "postgres"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  skip_final_snapshot    = var.environment != "prod"
  publicly_accessible    = false
}

# ------------------------------------------------------------------- ecr ---
resource "aws_ecr_repository" "backend" {
  name                 = "aetherai-backend"
  image_tag_mutability = "IMMUTABLE"
}

resource "aws_ecr_repository" "frontend" {
  name                 = "aetherai-frontend"
  image_tag_mutability = "IMMUTABLE"
}

# ------------------------------------------------------------------- ecs ---
resource "aws_ecs_cluster" "main" {
  name = "aetherai-${var.environment}"
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "aetherai-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name  = "backend"
    image = "${aws_ecr_repository.backend.repository_url}:latest"
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "ENV", value = var.environment },
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
      { name = "JWT_SECRET", valueFrom = aws_ssm_parameter.jwt_secret.arn },
    ]
  }])
}

resource "aws_ecs_service" "backend" {
  name            = "aetherai-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.backend_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.backend.id]
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "aetherai-ecs-execution-${var.environment}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ------------------------------------------------------------------ ssm ---
resource "aws_ssm_parameter" "database_url" {
  name  = "/aetherai/${var.environment}/database_url"
  type  = "SecureString"
  value = "postgresql+psycopg2://postgres:${var.db_password}@${aws_db_instance.postgres.address}:5432/aetherai"
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/aetherai/${var.environment}/jwt_secret"
  type  = "SecureString"
  value = var.jwt_secret
}
