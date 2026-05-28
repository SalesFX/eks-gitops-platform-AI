terraform {
  required_version = ">= 1.9"

  backend "s3" {
    bucket       = "devops-ia-production-terraform-state-<YOUR_ACCOUNT_ID>"
    key          = "addons/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
  }
}
