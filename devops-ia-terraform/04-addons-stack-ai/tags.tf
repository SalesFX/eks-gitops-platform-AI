locals {
  common_tags = {
    Environment = "production"
    Project     = "devops-ia"
    ManagedBy   = "terraform"
    Stack       = "addons"
    ADR         = "ADR-0007"
  }
}
