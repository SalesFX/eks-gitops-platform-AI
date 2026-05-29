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
from diagrams.k8s.network import Ingress

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
    # ── Terraform IaC (linha do topo) ─────────────────────────────────────────
    # Declarados em ordem reversa: graphviz inverte no eixo LR dentro de TB.
    with Cluster("Terraform IaC (provisionamento)"):
        tf4 = Terraform("04\naddons + LBC")
        tf3 = Terraform("03\nci-cd + OIDC")
        tf2 = Terraform("02\neks + ecr")
        tf1 = Terraform("01\nnetworking")
        tf0 = Terraform("00\nremote-backend")

    # ── Coluna esquerda: GitHub + GitHub Actions + IAM + ECR ─────────────────
    repo = Github("GitHub\ncode + manifests")

    with Cluster("GitHub Actions"):
        cicd = GithubActions("ci-cd.yml\nbuild + push images")
        sec  = GithubActions("security-scans.yml\nGitleaks · Checkov · Trivy")

    iam = IAMRole("IAM Role\nOIDC / GitHub")
    ecr = EC2ContainerRegistry("Amazon ECR\nbackend + frontend")

    # ── Coluna direita: Internet + VPC/EKS ───────────────────────────────────
    users = Users("Internet")

    with Cluster("VPC Multi-AZ  (AWS us-east-1)"):
        alb = ALB("ALB\ninternet-facing")

        with Cluster("EKS Cluster 1.32  |  t3.small x4  |  AL2023"):

            with Cluster("kube-system"):
                lbc = EKS("AWS Load Balancer\nController")

            with Cluster("namespace: argocd"):
                argo = Argocd("ArgoCD")

            with Cluster("namespace: default"):
                ing = Ingress("Ingress")
                fe  = Deployment("Frontend\n2 replicas")
                be  = Deployment("Backend .NET\n2 replicas")

            with Cluster("namespace: monitoring"):
                vmagent  = Prometheus("vmagent")
                vmsingle = Prometheus("vmsingle")
                grafana  = Grafana("Grafana")

    # ── Anchor invisivel: Terraform acima de tudo ─────────────────────────────
    # Edge invis com constraint=true ancora o rank de tf0 acima de repo e users.
    tf0 >> Edge(style="invis") >> repo
    tf0 >> Edge(style="invis") >> users

    # ── Coluna CI/CD: chain vertical (visible) ────────────────────────────────
    # sec nao tem edge externo; sem ancora ficaria acima de repo, arrastando
    # o cluster GitHub Actions para cima do GitHub. Edge invis resolve isso.
    repo >> Edge(style="invis") >> sec
    repo >> Edge(color="#444444") >> cicd
    cicd >> Edge(label="OIDC", color="#d6b656") >> iam
    iam  >> Edge(color="#d6b656") >> ecr

    # ── GitOps: GitHub → ArgoCD ───────────────────────────────────────────────
    repo >> Edge(
        color="#6a3d9a",
        penwidth="2.5",
        label="GitOps manifests",
        constraint="false",
    ) >> argo
    argo >> Edge(color="#9673a6", penwidth="1.5") >> ing
    argo >> Edge(color="#9673a6", penwidth="1.5") >> fe
    argo >> Edge(color="#9673a6", penwidth="1.5") >> be

    # ── Image pull: ECR → pods (nao desloca o ECR para dentro do EKS) ─────────
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> fe
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> be

    # ── LBC cria o ALB ────────────────────────────────────────────────────────
    lbc >> Edge(
        label="creates", style="dashed", color="#d6b656", constraint="false",
    ) >> alb

    # ── Coluna Runtime: chain vertical (visible) ──────────────────────────────
    users >> Edge(color="#4488cc", penwidth="1.5") >> alb
    alb   >> Edge(color="#4488cc", penwidth="1.5") >> ing
    ing   >> Edge(color="#82b366") >> fe
    ing   >> Edge(color="#82b366") >> be

    # ── Monitoring ────────────────────────────────────────────────────────────
    vmagent  >> Edge(color="#ae4132", constraint="false") >> vmsingle
    vmsingle >> Edge(color="#ae4132", constraint="false") >> grafana
