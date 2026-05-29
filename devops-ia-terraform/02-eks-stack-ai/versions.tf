terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "devops-ia-production-terraform-state-074994084847"
    key          = "eks/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
