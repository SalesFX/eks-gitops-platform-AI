# Runbook: ArgoCD Multi-App Pattern (Preparação para Fase 2)

**ADR de referência:** ADR-0012 (estrutura para Fase 2)
**Status:** Documentação de referência — implementar apenas na Fase 2
**Última revisão:** 2026-05-27

---

## Contexto

Na Fase 1, ArgoCD usa `Application` standalone apontando para o monorepo.
Este documento descreve o padrão para a Fase 2, quando o `gitops-repo` separado
e `ApplicationSet` serão adotados.

---

## Application standalone (padrão atual — Fase 1)

Usado atualmente em `03-ci-cd-stack-ai`. Cada app tem uma `Application` própria:

```yaml
# Exemplo de Application standalone (Fase 1 — monorepo)
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: backend
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/SalesFX/aws-devops-platform
    targetRevision: main
    path: devops-ia-kubernetes/backend
  destination:
    server: https://kubernetes.default.svc
    namespace: backend
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

---

## ApplicationSet com git generator (Fase 2 — gitops-repo)

O `ApplicationSet` com `git generator` descobre automaticamente aplicações por
diretório no repositório. Um único `ApplicationSet` substitui todas as `Application`
standalone:

```yaml
# gitops-repo/argocd/applicationset.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: devops-ia-apps
  namespace: argocd
spec:
  # goTemplating habilita expressões Go template para transformações de nome
  goTemplate: true
  goTemplateOptions:
    - "missingkey=error"
  generators:
    - git:
        repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
        revision: main
        directories:
          # Descobre todos os subdiretórios de devops-ia-kubernetes/ como apps
          - path: devops-ia-kubernetes/*
          # Excluir diretórios que não são apps (ex.: shared/, _base/)
          - path: devops-ia-kubernetes/_*
            exclude: true
  template:
    metadata:
      # Nome da Application = nome do diretório (ex.: "backend", "frontend")
      name: "{{.path.basename}}"
      namespace: argocd
      finalizers:
        - resources-finalizer.argocd.argoproj.io
    spec:
      project: default
      source:
        repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
        targetRevision: main
        path: "{{.path.path}}"
      destination:
        server: https://kubernetes.default.svc
        # Namespace = nome do app
        namespace: "{{.path.basename}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
          - PrunePropagationPolicy=foreground
```

---

## ApplicationSet com matrix generator (Fase 3 — multi-ambiente)

Quando houver múltiplos ambientes (production + staging), o `matrix generator`
combina a lista de ambientes com os diretórios de apps:

```yaml
# gitops-repo/argocd/applicationset-multi-env.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: devops-ia-apps-multi-env
  namespace: argocd
spec:
  goTemplate: true
  generators:
    - matrix:
        generators:
          # Generator 1: lista de ambientes
          - list:
              elements:
                - env: production
                  cluster: https://kubernetes.default.svc
                - env: staging
                  cluster: https://staging-cluster.example.com
          # Generator 2: diretórios de apps no gitops-repo
          - git:
              repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
              revision: main
              directories:
                - path: "environments/{{.env}}/*"
  template:
    metadata:
      name: "{{.env}}-{{.path.basename}}"
      namespace: argocd
    spec:
      project: default
      source:
        repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
        targetRevision: main
        path: "environments/{{.env}}/{{.path.basename}}"
      destination:
        server: "{{.cluster}}"
        namespace: "{{.path.basename}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
```

---

## Estrutura de diretórios no `gitops-repo` para multi-ambiente (Fase 3)

```
aws-devops-platform-gitops/
├── environments/
│   ├── production/
│   │   ├── backend/
│   │   │   ├── kustomization.yaml
│   │   │   └── patches/
│   │   │       └── image-tag.yaml   # atualizado pelo CI a cada build
│   │   └── frontend/
│   │       ├── kustomization.yaml
│   │       └── patches/
│   │           └── image-tag.yaml
│   └── staging/
│       ├── backend/
│       │   └── kustomization.yaml
│       └── frontend/
│           └── kustomization.yaml
├── base/
│   ├── backend/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── pdb.yaml
│   └── frontend/
│       ├── deployment.yaml
│       ├── service.yaml
│       └── pdb.yaml
└── argocd/
    └── applicationset.yaml
```

---

## Como verificar ApplicationSets em execução

```bash
# Listar ApplicationSets
kubectl get applicationset -n argocd

# Descrever ApplicationSet (ver Applications geradas)
kubectl describe applicationset devops-ia-apps -n argocd

# Listar Applications geradas
argocd app list | grep devops-ia

# Status de sincronização
argocd app get backend
argocd app get frontend
```

---

## Diferença entre Application e ApplicationSet

| Aspecto | Application | ApplicationSet |
|---------|-------------|----------------|
| Gerenciamento | Manual — 1 recurso por app | Automático — 1 ApplicationSet gera N Applications |
| Descoberta de apps | Estática (definida no manifest) | Dinâmica (por diretório, cluster, list) |
| Adição de novo app | Criar novo arquivo YAML | Apenas criar o diretório no gitops-repo |
| Remoção de app | Deletar o arquivo Application | Deletar o diretório no gitops-repo |
| Complexidade | Baixa | Média (requer entender generators) |
| Recomendado para | 1–3 apps, time pequeno | 4+ apps, múltiplos ambientes |

---

## Referências

- ADR-0012: Estratégia de Separação de Repositórios GitOps
- ADR-0006: ArgoCD GitOps Deployment
- ArgoCD ApplicationSet: https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/
- ArgoCD git generator: https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/generators-git/
- ArgoCD matrix generator: https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/generators-matrix/
