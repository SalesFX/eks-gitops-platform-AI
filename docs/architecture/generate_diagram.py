"""
Gera docs/architecture/architecture.png usando diagrams (diagrams.mingrammer.com).
Uso: python3 docs/architecture/generate_diagram.py
"""

from pathlib import Path
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EKS, EC2ContainerRegistry
from diagrams.aws.network import ALB
from diagrams.aws.security import IAMRole
from diagrams.onprem.gitops import Argocd
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.vcs import Github
from diagrams.onprem.iac import Terraform
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.onprem.client import Users
from diagrams.k8s.compute import Deployment

OUTPUT = str(Path(__file__).parent / "architecture")

graph_attr = {
    "bgcolor":  "white",
    "pad":      "0.8",
    "splines":  "ortho",
    "nodesep":  "0.8",
    "ranksep":  "1.0",
    "fontsize": "13",
    "fontname": "Helvetica",
    "newrank":  "true",
}

node_attr = {
    "fontsize": "10",
    "fontname": "Helvetica",
}

with Diagram(
    "devops-ia-production",
    filename=OUTPUT,
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
    show=False,
    direction="TB",
):
    # ── Terraform IaC (provisionamento) ───────────────────────────────────────
    # Declarados em ordem reversa: graphviz inverte a ordem de declaracao no
    # layout LR-dentro-de-TB, entao declarar 04..00 resulta em 00..04 na tela.
    with Cluster("Terraform IaC (provisionamento)"):
        tf4 = Terraform("04\naddons + LBC")
        tf3 = Terraform("03\nci-cd + OIDC")
        tf2 = Terraform("02\neks + ecr")
        tf1 = Terraform("01\nnetworking")
        tf0 = Terraform("00\nremote-backend")

    # ── Source of truth + CI/CD ───────────────────────────────────────────────
    repo = Github("GitHub\ncode + manifests")

    with Cluster("GitHub Actions"):
        cicd = GithubActions("ci-cd.yml\nbuild + push images")
        sec  = GithubActions("security-scans.yml\nGitleaks · Checkov · Trivy")

    # ── AWS ───────────────────────────────────────────────────────────────────
    with Cluster("AWS us-east-1"):
        iam = IAMRole("IAM Role\nOIDC / GitHub")
        ecr = EC2ContainerRegistry("Amazon ECR\nbackend + frontend")

        with Cluster("VPC Multi-AZ"):
            users = Users("Internet")
            alb   = ALB("ALB\ninternet-facing")

            with Cluster("EKS Cluster 1.32  |  t3.small x4  |  AL2023"):

                with Cluster("kube-system"):
                    lbc = EKS("AWS Load Balancer\nController")

                with Cluster("argocd"):
                    argo = Argocd("ArgoCD")

                with Cluster("default"):
                    fe = Deployment("Frontend\n2 replicas")
                    be = Deployment("Backend .NET\n2 replicas")

                with Cluster("monitoring"):
                    vmagent  = Prometheus("vmagent")
                    vmsingle = Prometheus("vmsingle")
                    grafana  = Grafana("Grafana")

    # ── Push -> build -> deploy ───────────────────────────────────────────────
    repo >> Edge(color="#444444") >> cicd
    cicd >> Edge(label="OIDC", color="#d6b656") >> iam
    iam  >> Edge(color="#d6b656", constraint="false") >> ecr

    # ── GitOps ────────────────────────────────────────────────────────────────
    argo >> Edge(color="#9673a6") >> fe
    argo >> Edge(color="#9673a6") >> be

    # ── Image pull (nao afeta layout) ─────────────────────────────────────────
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> fe
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> be

    # ── Acesso externo via ALB ────────────────────────────────────────────────
    users >> Edge(color="#4488cc") >> alb
    lbc   >> Edge(label="manages", style="dashed", color="#d6b656", constraint="false") >> alb
    alb   >> Edge(color="#82b366") >> fe
    alb   >> Edge(color="#82b366") >> be

    # ── Monitoring (constraint=false para manter nos dentro do cluster) ────────
    vmagent  >> Edge(color="#ae4132", constraint="false") >> vmsingle
    vmsingle >> Edge(color="#ae4132", constraint="false") >> grafana
