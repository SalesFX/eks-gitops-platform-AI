provider "aws" {
  region = var.cluster.region

  # Workaround WSL2: endpoint global do S3 pode sofrer TLS reset.
  # Forçamos o endpoint regional para evitar timeout no HeadBucket.
  endpoints {
    s3 = "https://s3.${var.cluster.region}.amazonaws.com"
  }

  default_tags {
    tags = local.common_tags
  }
}

provider "helm" {
  kubernetes = {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", var.cluster.name, "--region", var.cluster.region]
    }
  }
}

data "aws_eks_cluster" "this" {
  name = var.cluster.name
}

data "aws_eks_cluster_auth" "this" {
  name = var.cluster.name
}
