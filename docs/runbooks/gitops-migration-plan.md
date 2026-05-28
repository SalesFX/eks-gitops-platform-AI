# Runbook: Plano de Migração GitOps — Monorepo para 3 Repositórios

**ADR de referência:** ADR-0012 (Fase 2 — migração planejada, não implementar ainda)
**Status:** Documentação de preparação — Fase 1 (monorepo endurecido)
**Última revisão:** 2026-05-27

---

## Contexto

O projeto usa um monorepo por design consciente na Fase 1 (ADR-0012). Este documento
prepara a execução da Fase 2: separação em 3 repositórios. Não execute este plano até
que pelo menos um dos gatilhos abaixo seja atingido.

---

## Gatilhos para migração (qualquer um é suficiente)

- [ ] Time crescer para >= 2 desenvolvedores com responsabilidades distintas
- [ ] Necessidade de revogar acesso granular (ex.: contractor temporário)
- [ ] > 5 minutos médios de CI por mudança em `docs/` (ruído)
- [ ] >= 3 microserviços de aplicação no repositório

---

## Pré-requisitos antes de migrar

- [ ] Todos os workflows existentes têm path-filter corretos (nenhum job redundante em push de `docs/`)
- [ ] ArgoCD está sincronizando todos os apps com auto-sync + selfHeal + prune
- [ ] Branch protection em `main` está ativa com todos os status checks obrigatórios (ADR-0009)
- [ ] Terraform state está versionado e saudável (validar com `terraform plan` em todos os stacks)
- [ ] CODEOWNERS está criado (`.github/CODEOWNERS`) e revisores ativos
- [ ] Runbooks de rollback documentados e testados (ADR-0010)
- [ ] Janela de manutenção agendada: migração leva ~1 dia útil + 2 dias de observação

---

## Ordem de extração dos 3 repositórios

A ordem é crítica: manter GitOps funcional durante toda a migração.

### 1. `apps-repo` (SalesFX/aws-devops-platform-apps) — primeiro

Extrair primeiro porque é o menor risco: as aplicações continuam buildando mesmo
enquanto o GitOps aponta para o monorepo.

```bash
# No diretório do monorepo clonado em path temporário
git clone https://github.com/SalesFX/aws-devops-platform.git monorepo-migration
cd monorepo-migration

# Preservar histórico apenas dos paths relevantes
git filter-repo \
  --path devops-ia-apps/ \
  --path .github/workflows/ci-cd.yml \
  --path .github/workflows/security-scans.yml

# Criar novo repo vazio no GitHub antes deste passo
git remote set-url origin https://github.com/SalesFX/aws-devops-platform-apps.git
git push origin main
```

### 2. `gitops-repo` (SalesFX/aws-devops-platform-gitops) — segundo

Extrair segundo. ArgoCD ainda aponta para o monorepo — não mudar até este repo estar pronto.

```bash
cd monorepo-migration  # nova cópia limpa do monorepo

git filter-repo \
  --path devops-ia-kubernetes/ \
  --path argocd/

git remote set-url origin https://github.com/SalesFX/aws-devops-platform-gitops.git
git push origin main
```

### 3. `infra-repo` (SalesFX/aws-devops-platform-infra) — terceiro

Extrair por último. Terraform e docs ficam aqui.

```bash
cd monorepo-migration  # nova cópia limpa do monorepo

git filter-repo \
  --path devops-ia-terraform/ \
  --path docs/ \
  --path .claude/ \
  --path .github/workflows/  # adaptar para incluir apenas workflows de infra

git remote set-url origin https://github.com/SalesFX/aws-devops-platform-infra.git
git push origin main
```

### 4. Reconfigurar ArgoCD para apontar para `gitops-repo`

**Esta é a etapa crítica — apenas após `gitops-repo` estar validado.**

```bash
# Atualizar a Application do ArgoCD (ou criar ApplicationSet — ver runbook abaixo)
kubectl patch application backend -n argocd --type merge -p '{
  "spec": {
    "source": {
      "repoURL": "https://github.com/SalesFX/aws-devops-platform-gitops"
    }
  }
}'

kubectl patch application frontend -n argocd --type merge -p '{
  "spec": {
    "source": {
      "repoURL": "https://github.com/SalesFX/aws-devops-platform-gitops"
    }
  }
}'
```

### 5. Arquivar o monorepo (não deletar)

```bash
# Via GitHub UI ou gh CLI:
gh repo archive SalesFX/aws-devops-platform
```

---

## Como atualizar a pipeline CI/CD para push ao `gitops-repo`

Na Fase 2, após o build e push para ECR, o `apps-repo` deve abrir um PR no `gitops-repo`
para atualizar a tag de imagem em vez de commitar no próprio repo.

```yaml
# Trecho do novo job update-kustomization no apps-repo (Fase 2):
- name: Create PR in gitops-repo
  uses: peter-evans/create-pull-request@v6
  with:
    token: ${{ secrets.GITOPS_REPO_TOKEN }}  # PAT ou GitHub App token com write em gitops-repo
    repository: SalesFX/aws-devops-platform-gitops
    commit-message: "gitops: update ${{ matrix.app }} image to ${{ steps.meta.outputs.tag }}"
    branch: "update/${{ matrix.app }}-${{ steps.meta.outputs.tag }}"
    title: "Update ${{ matrix.app }} image to ${{ steps.meta.outputs.tag }}"
    body: |
      Automated image update triggered by build ${{ github.run_id }}
      App: ${{ matrix.app }}
      Tag: ${{ steps.meta.outputs.tag }}
      SHA: ${{ github.sha }}
    base: main
```

**Token recomendado:** GitHub App com escopo `contents: write` + `pull-requests: write`
apenas no `gitops-repo`. Não usar PAT pessoal.

---

## Como configurar ArgoCD ApplicationSet para múltiplos repos (Fase 2)

Ao migrar para `gitops-repo`, substituir as `Application` standalone por um `ApplicationSet`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: devops-ia-apps
  namespace: argocd
spec:
  generators:
    - git:
        repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
        revision: main
        directories:
          # Gera 1 Application por subdiretório direto de devops-ia-kubernetes/
          # Exemplo: devops-ia-kubernetes/backend -> Application "backend"
          - path: devops-ia-kubernetes/*
  template:
    metadata:
      name: "{{path.basename}}"
    spec:
      project: default
      source:
        repoURL: https://github.com/SalesFX/aws-devops-platform-gitops
        targetRevision: main
        path: "{{path}}"
      destination:
        server: https://kubernetes.default.svc
        namespace: "{{path.basename}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
```

---

## Estrutura do `gitops-repo` (Fase 2)

```
aws-devops-platform-gitops/
├── devops-ia-kubernetes/
│   ├── backend/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── pdb.yaml
│   │   └── kustomization.yaml
│   └── frontend/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── pdb.yaml
│       └── kustomization.yaml
└── argocd/
    └── applicationset.yaml
```

---

## Referências

- ADR-0012: Estratégia de Separação de Repositórios GitOps
- ADR-0006: ArgoCD GitOps Deployment
- ADR-0005: Pipeline CI/CD GitHub Actions
- git filter-repo: https://github.com/newren/git-filter-repo
- ArgoCD ApplicationSet: https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/
