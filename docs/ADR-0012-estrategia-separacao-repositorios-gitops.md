# ADR-0012: Estratégia de Separação de Repositórios GitOps — Manter Monorepo na Fase 1, Migrar para 3-repo em Fase 2

**Status:** Approved — manter monorepo na Fase 1 com refatoração interna; migração planejada para Fase 2
**Data:** 2026-05-27
**Autores:** [Architect Agent]
**Supersedes / Relacionado:** [[ADR-0005]] (Pipeline CI/CD), [[ADR-0006]] (ArgoCD GitOps), [[ADR-0009]] (Pipeline Security), [[ADR-0010]] (Rollback)

## Viabilidade Free Tier

> **Veredicto:** 100% viável — decisão organizacional de Git, **zero impacto no cluster** e zero custo direto.
>
> Justificativa: validado via `aws-mcp` em [Continuous Deployment and GitOps delivery with EKS Blueprints and ArgoCD](https://aws.amazon.com/blogs/containers/continuous-deployment-and-gitops-delivery-with-amazon-eks-blueprints-and-argocd/) que o padrão multi-repo (infra-repo + apps-repo + gitops-repo) é a referência da AWS para clusters EKS com múltiplas equipes — mas o **mesmo blog reconhece que monorepo é apropriado em estágios iniciais** quando há uma única equipe e nenhum requisito de acesso granular. GitHub é gratuito para repos públicos e privados ilimitados. Custos adicionais zero.

## Contexto

O repositório atual `eks-terraform-cicd-monitoring-001` (no GitHub `SalesFX/aws-devops-platform`) é um **monorepo** que contém quatro tipos de artefatos:

```text
eks-terraform-cicd-monitoring-001/   (monorepo atual)
├── devops-ia-terraform/             # Stacks Terraform (infra AWS)
│   ├── 00-remote-backend-stack-ai/
│   ├── 01-networking-stack-ai/
│   ├── 02-eks-stack-ai/
│   └── 03-ci-cd-stack-ai/
├── devops-ia-apps/                  # Código-fonte das aplicações
│   ├── frontend/                    # Next.js
│   └── backend/                     # .NET
├── devops-ia-kubernetes/            # Manifestos K8s + kustomization
│   ├── frontend/
│   ├── backend/
│   └── kustomization.yaml
├── .github/workflows/               # Pipelines CI/CD
└── docs/                            # ADRs, runbooks, implementation records
```

A pipeline ([[ADR-0005]]) faz path-filter por mudanças em `devops-ia-apps/{frontend,backend}/**`, builda, pusha para ECR, e **commita de volta** no próprio repo atualizando `devops-ia-kubernetes/kustomization.yaml`. ArgoCD ([[ADR-0006]]) observa esse arquivo e sincroniza no cluster.

### Por que avaliar separação agora

Embora o monorepo funcione bem hoje (1 desenvolvedor, escopo controlado), há fricções latentes que aparecem conforme o projeto cresce:

1. **Acoplamento de permissões**: quem tem write em `main` pode mudar tanto IAM/VPC (Terraform) quanto deployar app — viola least privilege.
2. **Ruído em CI**: todo push roda 9 jobs de segurança ([[ADR-0009]]), mesmo que a mudança seja só em `docs/`.
3. **Self-commit loop**: pipeline commita no mesmo repo que monitorou — pode disparar segunda execução se path filter não for cuidadoso.
4. **ArgoCD observando o repo que tem código-fonte**: ArgoCD precisa de read-only token com escopo amplo demais.
5. **Histórico Git misturado**: um `git log` mostra commits de infra, app e ADR juntos — dificulta rastreabilidade.
6. **CODEOWNERS por path** mitiga parcialmente, mas não resolve revogação de acesso (alguém que sai do time perde acesso a tudo).

### Validações via MCP

- **aws-mcp** — [Continuous Deployment and GitOps delivery with EKS Blueprints and ArgoCD](https://aws.amazon.com/blogs/containers/continuous-deployment-and-gitops-delivery-with-amazon-eks-blueprints-and-argocd/): a AWS recomenda **explicitamente** o padrão de múltiplos repositórios para casos com plataforma + times de aplicação separados, em particular: (1) repo de infra/plataforma, (2) repo de aplicação por time, (3) repo de manifests/GitOps. ArgoCD `ApplicationSet` é recomendado para gerar Applications dinamicamente a partir de gitops-repo.
- **aws-mcp** — [Deep dive: Streamlining GitOps with Amazon EKS capability for Argo CD](https://aws.amazon.com/blogs/containers/deep-dive-streamlining-gitops-with-amazon-eks-capability-for-argo-cd/): padrão hub-and-spoke onde um cluster central observa um único `gitops-repo` que define apps para múltiplos clusters. Reforça que o repositório de manifests **deve ser separado** do código-fonte.
- **aws-mcp** — [Argo CD concepts (EKS userguide)](https://docs.aws.amazon.com/eks/latest/userguide/argocd-concepts.html): `Application` aceita uma source por aplicação; `ApplicationSet` com generator `git` pode iterar sobre múltiplos paths/branches/repos.
- ArgoCD docs (referência fora MCP): `ApplicationSet` com `matrix generator` permite combinar `list generator` (lista de apps) com `git generator` (lista de paths no gitops-repo).

## Decisão

**Manter o monorepo atual na Fase 1**, com refatoração interna para reduzir fricção, e **migrar para 3 repositórios em Fase 2** quando os gatilhos definidos forem atingidos.

### Por que NÃO migrar agora

1. **Time = 1 pessoa**. Custo de migração (criar repos, ajustar CI, mudar ArgoCD source, atualizar docs) é alto e benefício marginal.
2. **Sem requisito de acesso granular** ativo — não há outro desenvolvedor para isolar.
3. **Migração no momento errado adiciona risco** — quebrar GitOps em produção do portfolio não compensa o ganho organizacional ainda inexistente.
4. **Padrões de path filter já mitigam ruído** em CI.

### Refatoração interna na Fase 1 (escolhida, implementável imediatamente)

Manter o monorepo, mas **endurecer fronteiras internas** com CODEOWNERS e branch protection:

1. **CODEOWNERS por diretório** (precisão maior que hoje):
   ```text
   # .github/CODEOWNERS
   /devops-ia-terraform/      @SalesFX   # Infra — exige revisão obrigatória
   /devops-ia-apps/frontend/  @SalesFX   # App owner
   /devops-ia-apps/backend/   @SalesFX   # App owner
   /devops-ia-kubernetes/     @SalesFX   # GitOps manifests
   /.github/workflows/        @SalesFX   # CI/CD owner
   /docs/                     @SalesFX   # Arquitetura
   ```
2. **Branch protection em `main`**: ver [[ADR-0009]] — exige status checks, 1 review, conversations resolved, linear history.
3. **Path-filter agressivo nos workflows**: cada job só roda quando o path muda. Reduz ruído imediato.
4. **Convenção de commits**: prefixo por escopo (`infra:`, `app/frontend:`, `app/backend:`, `gitops:`, `ci:`, `docs:`). Facilita filtros de `git log` e auditoria.
5. **Pipeline self-commit usa `[skip ci]`** na mensagem do commit que ArgoCD vai sincronizar — evita loop ([[ADR-0005]] já cobre isso).
6. **ArgoCD continua apontando para o monorepo** com `path: devops-ia-kubernetes`. Não há mudança.

### Estrutura-alvo (Fase 2 — quando os gatilhos forem atingidos)

```text
┌─────────────────────────────────────────────────────────────┐
│  infra-repo                                                 │
│  (SalesFX/aws-devops-platform-infra)                        │
│  • Terraform stacks                                         │
│  • ADRs (docs/)                                             │
│  • Runbooks                                                 │
│  • Pipeline: terraform plan/apply, IaC scans                │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ provisiona
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  apps-repo                                                  │
│  (SalesFX/aws-devops-platform-apps)                         │
│  • devops-ia-apps/frontend (Next.js)                        │
│  • devops-ia-apps/backend  (.NET)                           │
│  • Pipeline: build, scan, push ECR                          │
│  • Pipeline: PR para gitops-repo atualizando kustomization  │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ build & push (ECR) + PR
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  gitops-repo                                                │
│  (SalesFX/aws-devops-platform-gitops)                       │
│  • devops-ia-kubernetes/                                    │
│  • argocd/applicationset.yaml                               │
│  • envs/{production,staging}/                               │
│  • Pipeline: validação de manifests (Checkov, kubeval)      │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ ArgoCD observa
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Cluster EKS  (devops-ia-production)                        │
└─────────────────────────────────────────────────────────────┘
```

### Como ArgoCD passa a referenciar múltiplos repos (Fase 2)

**Decisão: usar `ApplicationSet` com `git generator` apontando para o `gitops-repo` em path-based discovery**, em vez de múltiplas `Application` standalone:

```yaml
# Esboço conceitual (não é manifest pronto para deploy)
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
          - path: devops-ia-kubernetes/*  # gera 1 Application por subdir (frontend, backend, etc.)
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
```

**Application com multi-source** (alternativa, rejeitada para este projeto):
- ArgoCD 2.6+ suporta `spec.sources: []` com múltiplos repos (ex.: Helm chart de um repo + values de outro). Útil para gerenciar charts upstream — **fora do escopo** deste projeto, já que usamos manifests vanilla, não Helm para as apps.

### Estratégia de branches por repositório

| Repo | Branch | Ambiente | Trigger |
|---|---|---|---|
| `infra-repo` | `main` | Production AWS | PR-only; tag `v*` opcional para release notes |
| `infra-repo` | `staging` | (opcional Fase 3+) | Apenas se houver conta AWS de staging |
| `apps-repo` | `main` | Build → push para ECR `production/*` | PR-only |
| `apps-repo` | `staging` | Build → push para ECR `staging/*` (Fase 3) | PR direto ou auto-merge de develop |
| `gitops-repo` | `main` | Production cluster | Aceita PRs do `apps-repo` (auto, via bot) e humanos |
| `gitops-repo` | `staging` | Staging cluster (Fase 3) | Análogo |

**Promoção entre ambientes** (Fase 3): merge de `staging → main` no `gitops-repo` promove versões testadas em homologação para produção.

### CODEOWNERS por repositório (Fase 2)

| Repo | CODEOWNERS principal | Justificativa |
|---|---|---|
| `infra-repo` | Platform/SRE team | Mudanças em VPC, EKS, IAM exigem revisão de quem entende infra |
| `apps-repo` | Equipes de aplicação (frontend / backend separados via path) | Desenvolvedores donos de suas apps |
| `gitops-repo` | Platform/SRE team + leads de aplicação | Bot do `apps-repo` pode dar push automático em paths específicos via app token |

### Branch protection (Fase 2)

Comum a todos os 3 repos:
- Require pull request review (mínimo 1, exceto bot do CI em `gitops-repo`)
- Require status checks (security scans de [[ADR-0009]] adaptados ao escopo do repo)
- Require linear history
- Restrict push to specific roles (admins não fazem direct push em `main`)
- Require signed commits (Fase 3, quando time for > 1 dev)

### Impacto na pipeline CI/CD (Fase 2)

**Hoje** ([[ADR-0005]]): build + push + commit em `devops-ia-kubernetes/kustomization.yaml` no mesmo repo.

**Fase 2** (3-repo):
1. `apps-repo` faz build + push para ECR.
2. `apps-repo` cria **PR no `gitops-repo`** (via app token / GitHub App) atualizando `devops-ia-kubernetes/<app>/kustomization.yaml` com nova tag.
3. PR no `gitops-repo` roda validação de manifest (kubeval + Checkov K8s).
4. Auto-merge se checks passam **e** o autor do PR é o bot do CI **e** path muda apenas `image:` field (regra de segurança).
5. ArgoCD detecta merge e sincroniza.

**Token usado pelo bot** (decisão de segurança):
- GitHub App instalado no `apps-repo` e `gitops-repo`, com permissões mínimas (`contents: write`, `pull-requests: write`) em `gitops-repo`; `contents: read` em `apps-repo`.
- **Não usar PAT** (Personal Access Token) — viola least privilege e expira sem rotação.

### Ordem de migração (Fase 2)

1. **Criar `infra-repo` (vazio)** com mesma estrutura de diretórios.
2. `git filter-repo --path devops-ia-terraform/ --path docs/ --path .claude/` para preservar histórico.
3. Push para o novo `infra-repo`.
4. **Criar `gitops-repo` (vazio)**.
5. `git filter-repo --path devops-ia-kubernetes/`.
6. Push.
7. **Criar `apps-repo` (vazio)**.
8. `git filter-repo --path devops-ia-apps/ --path .github/workflows/build-*.yml`.
9. Push.
10. **Reconfigurar ArgoCD** para apontar para `gitops-repo` (atualizar `Application` ou criar `ApplicationSet`).
11. **Validar GitOps end-to-end**: push fake no `apps-repo`, ver PR aparecer em `gitops-repo`, ver ArgoCD sincronizar.
12. **Arquivar monorepo antigo** (não deletar — preserva histórico e referências de ADRs).
13. Atualizar documentação (README de cada repo, ADR-0012 como `Superseded by ADR-XXXX` se necessário).

**Janela**: ~1 dia útil para migração + 2 dias de observação. **Sem downtime** do cluster — GitOps continua sincronizando enquanto o repo é trocado.

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: 3 repos = 3 fronteiras de responsabilidade claras; CODEOWNERS por repo; auditoria simplificada.
2. **Security**: least privilege — desenvolvedor de app não tem write em infra; bot do CI tem token escopado; revogação de acesso por repo.
3. **Reliability**: zero impacto em runtime; ArgoCD continua observando manifests; rollback ([[ADR-0010]]) inalterado.
4. **Performance Efficiency**: CI rodando apenas no escopo necessário; build de app não dispara plan de Terraform.
5. **Cost Optimization**: zero custo direto; reduz minutos gastos em CI desnecessário.
6. **Sustainability**: menos compute ocioso em CI.

## Configuração Mínima Adotada (Fase 1, monorepo endurecido)

```text
Branch protection em main:
  - Require PR review:           1 approval
  - Require status checks:       todos os jobs de ADR-0009
  - Require linear history:      ON
  - Allow force pushes:          OFF
  - Allow deletions:             OFF

CODEOWNERS:                       1 owner por diretório
Path-filter:                      em cada workflow (já existe; refinar)
Commit convention:                infra: / app/frontend: / app/backend: / gitops: / ci: / docs:
Self-commit skip:                 [skip ci] no commit do bot (já em ADR-0005)
ArgoCD source:                    SalesFX/aws-devops-platform, path: devops-ia-kubernetes
```

## Consequências

### Positivas

- Sem custo, sem downtime, sem retrabalho em Fase 1.
- Refatoração interna (CODEOWNERS + path filters + commit convention) já entrega 60% do valor de uma separação completa.
- Fase 2 planejada e documentada — quando o gatilho disparar, há plano executável.
- Histórico preservável via `git filter-repo` (mantém commits relevantes).

### Negativas / Trade-offs

- **Acoplamento permanece**: write em `main` permite tudo. Mitigado parcialmente por CODEOWNERS, mas CODEOWNERS é regra de revisão, não de permissão.
- **CI permanece pesado em mudanças cross-diretório**: se uma feature toca infra+app+manifests, todos os jobs rodam.
- **Bot do CI continua com escopo amplo** (write em `main` do próprio repo) — mitigado por `[skip ci]` e path filter, mas filosoficamente errado.
- **Migração futura tem custo** (~1 dia útil + 2 dias de observação) — aceito conscientemente como dívida planejada.

## Alternativas Consideradas

| Alternativa | Motivo da rejeição |
|---|---|
| **Migrar para 3-repo agora** | Time = 1, sem necessidade granular de acesso; custo de migração > benefício imediato; risco de quebrar GitOps em meio à fase de portfolio. |
| **Migrar para 2-repo (apps + infra, manifests dentro do apps)** | ArgoCD apontando para apps-repo viola o padrão de manifests separados; bot do CI continua comitando no mesmo repo que ArgoCD observa — mesmo problema de hoje. |
| **Migrar para 4-repo (infra + apps + gitops + docs)** | Docs em repo separado fragmenta ADRs do contexto de implementação. ADR é artefato de arquitetura, naturalmente pertence ao infra-repo. |
| **Repo monolítico, mas com submodules** | Submodules adicionam complexidade significativa (clone --recursive, sincronização) sem ganho de isolamento real. Anti-padrão para GitOps. |
| **GitHub Org com fine-grained permissions, mantendo monorepo** | Fine-grained PAT existe mas é por usuário; não substitui separação de repos para times. Solução parcial. |

## Roadmap de Evolução

| Fase | Gatilho | O que adicionar |
|---|---|---|
| **Fase 1 (atual — monorepo endurecido)** | — | CODEOWNERS por diretório + branch protection + path-filter agressivo + commit convention. |
| **Fase 2 (3-repo)** | Pelo menos UM dos seguintes ocorrer:<br>• Time crescer para ≥ 2 desenvolvedores com responsabilidades distintas<br>• Necessidade de revogar acesso granular (ex.: contractor temporário)<br>• > 5 minutos médios em CI por mudança em `docs/` (ruído)<br>• Múltiplos serviços de app (≥ 3 microserviços) | Migrar via `git filter-repo`. Reconfigurar ArgoCD com `ApplicationSet` + git generator. Bot do CI via GitHub App com scope mínimo. |
| **Fase 3 (multi-environment)** | Necessidade de staging real (conta AWS separada) | Adicionar branch `staging` em `gitops-repo`; ApplicationSet com matrix generator (env × app); promoção via PR staging→main. |
| **Fase 4 (multi-cluster ou multi-region)** | Cluster passivo de DR ou expansão geográfica | Hub-and-spoke ArgoCD (1 cluster hub observa gitops-repo, sincroniza N spokes). Considerar **EKS Capability for Argo CD** (managed ArgoCD em hub-and-spoke) para evitar operar ArgoCD HA. |
| **Fase 5 (escala enterprise)** | Múltiplas linhas de produto | Considerar separação adicional por linha de produto (`apps-frontend-platform-repo`, `apps-checkout-repo`, etc.). Avaliar Backstage para registry de serviços. |

## Critérios de Aceitação

### Fase 1 (a implementar agora)

- [ ] `.github/CODEOWNERS` criado com regras por diretório listadas acima.
- [ ] Branch protection em `main` configurada (espelhando [[ADR-0009]]).
- [ ] Convenção de commits documentada em `docs/contributing/commit-convention.md`.
- [ ] Path-filter revisado em todos os workflows — confirmar que push em `docs/` não dispara `build-*`.
- [ ] README do repo atualizado com seção "Repository structure" explicando que é monorepo por design (Fase 1) com plano para Fase 2.

### Fase 2 (a implementar quando gatilho disparar)

- [ ] Três novos repositórios criados em `SalesFX/` (privados ou públicos conforme política do projeto).
- [ ] Histórico preservado via `git filter-repo`.
- [ ] ArgoCD reconfigurado com `ApplicationSet` apontando para `gitops-repo`.
- [ ] GitHub App de CI criado com scope mínimo.
- [ ] Teste end-to-end: PR no `apps-repo` → PR auto-criado no `gitops-repo` → merge → ArgoCD sync → pod novo rodando.
- [ ] Monorepo arquivado (não deletado) com README explicando a migração.
- [ ] ADR de implementação produzido pelo `devops-senior-engineer` em `docs/implementation/IMPL-ADR-0012-<data>.md`.

## Referências

- AWS Containers Blog — Continuous Deployment e GitOps com EKS Blueprints + ArgoCD (validado via aws-mcp): https://aws.amazon.com/blogs/containers/continuous-deployment-and-gitops-delivery-with-amazon-eks-blueprints-and-argocd/
- AWS Containers Blog — EKS Capability for Argo CD deep dive (validado via aws-mcp): https://aws.amazon.com/blogs/containers/deep-dive-streamlining-gitops-with-amazon-eks-capability-for-argo-cd/
- AWS EKS Userguide — Argo CD concepts (validado via aws-mcp): https://docs.aws.amazon.com/eks/latest/userguide/argocd-concepts.html
- ArgoCD ApplicationSet docs: https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/
- `git filter-repo` (preserva history em split): https://github.com/newren/git-filter-repo
- Relacionados: [[ADR-0005]] (Pipeline CI/CD), [[ADR-0006]] (ArgoCD), [[ADR-0009]] (Pipeline Security), [[ADR-0010]] (Rollback)
