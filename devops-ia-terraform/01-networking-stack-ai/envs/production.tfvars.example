aws_region = "us-east-1"

project = {
  name        = "devops-ia"
  environment = "production"
}

vpc = {
  name                    = "devops-ia-production"
  cidr                    = "10.0.0.0/16"
  public_subnet_cidrs     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  private_subnet_cidrs    = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
  availability_zones      = ["us-east-1a", "us-east-1b", "us-east-1c"]
  enable_flow_logs        = true
  flow_log_retention_days = 90
}
