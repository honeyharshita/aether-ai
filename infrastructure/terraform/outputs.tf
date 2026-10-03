output "database_endpoint" {
  value       = aws_db_instance.postgres.address
  description = "RDS Postgres endpoint"
}

output "backend_ecr_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "frontend_ecr_url" {
  value = aws_ecr_repository.frontend.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}
