provider "aws" {
  region = var.aws_region

  # Workaround WSL2: endpoint global do S3 pode sofrer TLS reset.
  # Forçamos o endpoint regional para evitar timeout no HeadBucket.
  endpoints {
    s3 = "https://s3.${var.aws_region}.amazonaws.com"
  }

  default_tags {
    tags = local.common_tags
  }
}

data "terraform_remote_state" "networking" {
  backend = "s3"
  config = {
    bucket = "devops-ia-production-terraform-state-074994084847"
    key    = "networking/terraform.tfstate"
    region = "us-east-1"
  }
}
