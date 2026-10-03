environment            = "dev"
aws_region              = "us-east-1"
db_instance_class       = "db.t3.micro"
backend_desired_count   = 1
# db_password and jwt_secret: pass via -var or TF_VAR_ env vars, never commit them
