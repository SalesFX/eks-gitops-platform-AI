# ADR-0009: Segurança da Pipeline e Scans Automatizados (SAST, SCA, Container, IaC, Secrets)

**Status:** Approved — implementação imediata
**Data:** 2026-05-27
**Autores:** [Architect Agent]
**Supersedes / Relacionado:** [[ADR-0004]] (OIDC GitHub→AWS), [[ADR-0005]] (Pipeline CI/CD GitHub Actions), [[ADR-0006]] (ArgoCD GitOps)

## Viabilidade Free Tier

> **Veredicto:** Viável agora — 100% executado nos runners do GitHub Actions, **zero impacto** nos nodes EKS.
>
> Justificativa: validado via `aws-mcp` que o caminho recomendado pela AWS para scan de containers em pipeline é Trivy/Inspector em build-time (CodeBuild/Actions), antes do push para o ECR. Todos os scanners citados (Trivy, Gitleaks, Checkov, Semgrep CE, npm audit, dotnet list package --vulnerable) são open source ou têm tiers gratuitos. GitHub Actions free tier para repositórios **públicos** = ilimitado em minutos; para **privados** = 2.000 min/mês na conta Free. SARIF upload para GitHub Security tab é gratuito em repositórios públicos e incluso no GitHub Advanced Security em privados (este projeto, conforme [[ADR-0005]], está em repo público `SalesFX/aws-devops-platform`).

## Contexto

A pipeline atual ([[ADR-0005]]) faz build do backend (.NET) e frontend (Next.js), push para ECR (`devops-ia/production/{backend,frontend}`) e atualiza `kustomization.yaml` para o ArgoCD sincronizar. Hoje **nenhum scan automatizado** roda antes do push:

1. **Imagens podem ir para o ECR com CVEs CRITICAL conhecidos** — risco de explorar vulnerabilidade em produção via supply chain.
2. **Segredos podem vazar para o repositório público** sem detecção automática (ex.: AWS Access Keys, JWT secrets, connection strings).
3. **Terraform pode introduzir misconfiguration** (S3 bucket público acidental, security group `0.0.0.0/0:22`, falta de criptografia em RDS/EBS).
4. **Dependências vulneráveis** podem ser introduzidas sem sinalização (npm/NuGet package com CVE conhecida).
5. **Não há trilha de auditoria** dos achados ao longo do tempo — sem SARIF agregado.

O cluster está em `t3.micro x2` (restrição free tier), então toda a estratégia precisa **rodar nos runners do GitHub**, não no cluster. Felizmente esse é o padrão correto de "shift-left security" — scans devem rodar **antes** do deploy, não em runtime.

### Validações via MCP

- **aws-mcp** — [Scanning images with Trivy](https://aws.amazon.com/blogs/containers/scanning-images-with-trivy-in-an-aws-codepipeline/): a AWS recomenda explicitamente Trivy como scanner de container em pipelines, com política de bloqueio em CRITICAL. O padrão valida que push para o ECR **só ocorre** se o scan passar.
- **aws-mcp** — [Amazon Inspector for ECR](https://aws.amazon.com/inspector/faqs/): Amazon Inspector oferece scan automático de imagens no ECR pós-push (Enhanced scanning), com custo de **US$ 0,09 por imagem inicial + US$ 0,01 por re-scan**. Para 2 imagens × ~30 pushes/mês = US$ 6/mês. **Decisão**: usar Trivy pré-push (gratuito, bloqueia antes); Inspector pós-push fica como roadmap.
- **terraform-mcp** — Checkov tem provider Terraform oficial (`bridgecrew/checkov` em registries) e GitHub Action mantida (`bridgecrewio/checkov-action`). Validado em projeto Terraform com providers `hashicorp/aws ~> 6.0`.
- **GitHub Actions**:
  - `aquasecurity/trivy-action` — mantido oficialmente pela AquaSec (autora do Trivy), suporta SARIF output, exit-code configurável por severidade.
  - `gitleaks/gitleaks-action` — oficial do projeto Gitleaks, gratuito para repos públicos (organizações privadas requerem licença).
  - `bridgecrewio/checkov-action` — oficial; SARIF nativo.
  - `returntocorp/semgrep-action` — tier gratuito Semgrep CE com regras curated.
  - `github/codeql-action/upload-sarif@v3` — gratuito em repos públicos.

## Decisão

Implementar um workflow consolidado **`security-scans.yml`** no GitHub Actions que executa em paralelo aos jobs de build de [[ADR-0005]], com **gate de bloqueio em CRITICAL** para merge em `main`.

### Matriz de scanners

| Categoria | Ferramenta | Escopo | Trigger | Severidade que bloqueia |
|---|---|---|---|---|
| **Container scan** | `aquasecurity/trivy-action@0.24.0` | Imagens backend (.NET) e frontend (Next.js) pós-build, antes do push para ECR | PR → main, push → main | `CRITICAL` |
| **SCA — Frontend** | `npm audit --audit-level=high` + Trivy `fs` scan | `devops-ia-apps/frontend/package-lock.json` | PR → main, push → main | `CRITICAL` (HIGH = warning) |
| **SCA — Backend** | `dotnet list package --vulnerable --include-transitive` + Trivy `fs` | `devops-ia-apps/backend/*.csproj` | PR → main, push → main | `CRITICAL` (HIGH = warning) |
| **IaC scan** | `bridgecrewio/checkov-action@v12` | `devops-ia-terraform/**/*.tf` | PR → main, push → main em path filter `devops-ia-terraform/**` | severidade `HIGH`/`CRITICAL` em recursos AWS |
| **Secret scan** | `gitleaks/gitleaks-action@v2` | Repo inteiro + commits do PR | PR → main, push → main, schedule diário | qualquer detecção real (com allowlist) |
| **SAST (código)** | `semgrep/semgrep-action@v1` com ruleset `p/owasp-top-ten`, `p/csharp`, `p/typescript` | `devops-ia-apps/**` | PR → main, push → main | `ERROR` (severidade Semgrep) |
| **K8s manifest scan** | `bridgecrewio/checkov-action@v12` com framework `kubernetes` | `devops-ia-kubernetes/**/*.yaml` | PR → main, push → main em path filter | `HIGH`/`CRITICAL` |

### Política de severidade (decidida)

| Severidade | Comportamento na pipeline |
|---|---|
| `CRITICAL` | **Bloqueia merge**. Falha o job. Notifica via GitHub PR comment. |
| `HIGH` | **Warning**. Pipeline passa, mas comentário no PR sinaliza para revisão manual. Métrica gravada para SLA de 7 dias para mitigação. |
| `MEDIUM` / `LOW` / `INFO` | Apenas reporta no SARIF (sem bloqueio, sem comentário). |
| Vulnerabilidade sem fix disponível | Pode ser ignorada via arquivo `.trivyignore` com justificativa em comentário e expiry date (`# expires: 2026-08-27`). |

### Estrutura proposta de workflow (esqueleto declarativo — implementação fica para o `devops-senior-engineer`)

```text
.github/workflows/
├── ci-build-push.yml          # existente (ADR-0005)
├── security-scans.yml         # NOVO — este ADR
└── security-scheduled.yml     # NOVO — execução diária às 06:00 UTC

Jobs em security-scans.yml (rodam em paralelo onde possível):
  - secret-scan        (Gitleaks, runs-on: ubuntu-latest)
  - iac-tf-scan        (Checkov terraform, path filter)
  - iac-k8s-scan       (Checkov kubernetes, path filter)
  - sast-frontend      (Semgrep p/typescript + p/owasp-top-ten)
  - sast-backend       (Semgrep p/csharp + p/owasp-top-ten)
  - sca-frontend       (npm audit + trivy fs)
  - sca-backend        (dotnet list + trivy fs)
  - container-frontend (trivy image — depende do build-frontend de ci-build-push)
  - container-backend  (trivy image — depende do build-backend de ci-build-push)

Cada job:
  - upload-artifact: <tool>.sarif
  - github/codeql-action/upload-sarif: tab Security
  - falha com exit-code != 0 se CRITICAL encontrado
```

### Gate no fluxo de merge

- **Branch protection rule** em `main` exigindo:
  - Status checks obrigatórios: `secret-scan`, `iac-tf-scan`, `iac-k8s-scan`, `container-frontend`, `container-backend`
  - 1 approval mínimo
  - Conversations resolved
  - Linear history (sem merge commits)
- **CODEOWNERS** com revisor obrigatório para mudanças em `.github/workflows/`, `devops-ia-terraform/`, e `docs/`.

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: scans automatizados eliminam dependência de revisão manual de segurança em todo PR. SARIF agregado no GitHub Security tab fornece view única dos achados.
2. **Security**: cobre 5 categorias do shift-left (secrets, SAST, SCA, container, IaC). Bloqueio em CRITICAL impede que imagens com CVE conhecido cheguem ao ECR — quebra o vetor de supply chain. Reaproveita IRSA de [[ADR-0004]] (sem credenciais long-lived).
3. **Reliability**: scans rodam em runners isolados — falha de um scanner não impacta o build/push. Schedule diário (`security-scheduled.yml`) detecta CVEs publicados após o último commit (drift).
4. **Performance Efficiency**: Trivy + Semgrep + Checkov são leves no runner (cada um ~30–90s); paralelização mantém o ciclo do PR < 5 min.
5. **Cost Optimization**: zero custo extra (runners free; SARIF upload free em repo público). Adia Amazon Inspector (US$ 6/mês) para Fase 2.
6. **Sustainability**: dispensa scan contínuo no cluster — scans rodam só em build, on-demand. Sem ciclos ociosos.

## Configuração Mínima Adotada

```yaml
# Versões pinadas a serem usadas pelo devops-senior-engineer (validadas em 2026-05-27):
- aquasecurity/trivy-action@0.24.0   # severity CRITICAL,HIGH; exit-code 1 só em CRITICAL
- bridgecrewio/checkov-action@v12    # frameworks: terraform,kubernetes,dockerfile,secrets
- gitleaks/gitleaks-action@v2        # GITLEAKS_LICENSE não necessário em repo público
- returntocorp/semgrep-action@v1     # rulesets: p/owasp-top-ten + p/csharp + p/typescript
- github/codeql-action/upload-sarif@v3
```

Allowlist files a serem criados pelo `devops-senior-engineer`:
- `.trivyignore` — CVEs com justificativa e expiry.
- `.gitleaks.toml` — paths a ignorar (ex.: `**/testdata/**`).
- `.checkov.yml` — skip-checks com referência a ADRs justificando.
- `.semgrepignore` — opcional, para test fixtures.

## Consequências

### Positivas

- Vulnerabilidades CRITICAL bloqueadas antes do ECR — quebra de cadeia de supply chain.
- Visibilidade unificada de findings no GitHub Security tab.
- Detecção de secrets vazados em < 60s do commit.
- Auditável: SARIF retém histórico via GitHub Code Scanning.
- Custo direto **US$ 0,00**.

### Negativas / Trade-offs

- **Tempo total do PR aumenta** ~3–5 min (paralelizado bem; serializado seria 10+ min).
- **Falsos positivos** vão aparecer principalmente em Checkov K8s — exigirá curadoria do `.checkov.yml`.
- **Schedule diário gera novos achados** sem novos commits (drift por novos CVEs publicados) — exige rotina semanal de triagem.
- **Bloqueio em CRITICAL pode parar releases** quando upstream demora a publicar patch — mitigação: `.trivyignore` com expiry e justificativa documentada.
- **Sem cobertura de runtime** (CRDs comprometidos pós-deploy) — coberto parcialmente por [[ADR-0011]] (Kyverno em modo Audit) e [[ADR-0008]] (logs centralizados).

## Alternativas Consideradas

| Alternativa | Motivo da rejeição |
|---|---|
| **Amazon Inspector** (pós-push em ECR) | US$ ~6/mês para 2 imagens × ~30 pushes/mês. Não bloqueia push (detecta após). Reavaliar em Fase 2 quando budget permitir. |
| **Snyk** | SaaS pago acima de 200 testes/mês. Excelente UX mas vendor lock-in e custo recorrente. |
| **GitHub Advanced Security** (CodeQL completo) | Pago em repos privados (~US$ 49/active committer/mês). Em repo público é grátis — pode ser **adicionado em paralelo** ao Semgrep sem custo. Decisão: incluir CodeQL no roadmap Fase 2 como cobertura SAST premium. |
| **Sonarqube CE self-hosted** | Footprint de ~1–2 GiB RAM, não cabe no cluster `t3.micro`. Exigiria infra dedicada. |
| **Rodar Trivy como DaemonSet no cluster** | Conflita com restrição de RAM `t3.micro`. Trivy em build-time é o padrão recomendado pela própria AWS. |
| **Skip de scans em hotfix** | Pode ser tentador, mas remove o gate exatamente quando o risco é maior. Decisão: scans são obrigatórios mesmo em hotfix; usar `.trivyignore` com expiry para CVE sem fix. |

## Roadmap de Evolução

| Fase | Gatilho | O que adicionar |
|---|---|---|
| **Fase 1 (atual)** | — | Trivy + Checkov + Gitleaks + Semgrep CE + npm/dotnet audit. SARIF para GitHub Security. Bloqueio em CRITICAL. |
| **Fase 2** | Aprovação de budget de ~US$ 10–20/mês de segurança | Habilitar **Amazon Inspector Enhanced** no ECR (scan contínuo pós-push, com EventBridge → SNS). Adicionar **GitHub CodeQL** como segundo SAST (em paralelo a Semgrep). Adicionar **trivy-operator** no cluster (após upgrade para t3.medium) para scan de imagens em execução. |
| **Fase 3 (produção real)** | Compliance (SOC2/ISO 27001) ou expansão de time | **SBOM** (Software Bill of Materials) gerado pelo Trivy em formato SPDX/CycloneDX, armazenado em S3 com versionamento. Integração com **AWS Security Hub** para correlação. Considerar **Wiz**, **Lacework** ou **Prisma Cloud** se compliance exigir. **Sigstore/cosign** para image signing e verificação em admission webhook (via Kyverno verifyImages, ver [[ADR-0011]]). |
| **Fase 4 (multi-cluster/multi-account)** | Múltiplas contas AWS | Inspector multi-account via AWS Organizations; Security Hub agregado em conta de auditoria; threshold dinâmico baseado em CVSS environmental score. |

## Critérios de Aceitação

- [ ] Workflow `.github/workflows/security-scans.yml` criado com os 9 jobs descritos.
- [ ] Workflow `.github/workflows/security-scheduled.yml` rodando às 06:00 UTC diariamente em `main`.
- [ ] Branch protection em `main` exige todos os jobs de segurança como status checks obrigatórios.
- [ ] SARIF de Trivy, Checkov, Semgrep e Gitleaks aparecem na aba "Security → Code scanning alerts".
- [ ] Test case: PR com imagem contendo `CVE CRITICAL` é bloqueado (validar com imagem proposital `vulhub/log4j-shell-poc:latest` ou similar).
- [ ] Test case: PR com `AKIA...` mockado em commit é bloqueado pelo Gitleaks.
- [ ] Test case: PR com `aws_s3_bucket` sem `versioning` é sinalizado por Checkov.
- [ ] `.trivyignore`, `.gitleaks.toml`, `.checkov.yml`, `.semgrepignore` criados com pelo menos um exemplo comentado.
- [ ] CODEOWNERS criado exigindo revisão para `.github/workflows/`, `devops-ia-terraform/`, `docs/`.
- [ ] Documentado em `docs/runbooks/security-findings-triage.md` o fluxo de triagem semanal dos findings HIGH.

## Referências

- AWS Containers Blog — Trivy em pipeline (validado via aws-mcp): https://aws.amazon.com/blogs/containers/scanning-images-with-trivy-in-an-aws-codepipeline/
- Amazon Inspector FAQ (validado via aws-mcp): https://aws.amazon.com/inspector/faqs/
- Trivy: https://github.com/aquasecurity/trivy
- Checkov: https://www.checkov.io/
- Gitleaks: https://github.com/gitleaks/gitleaks
- Semgrep CE: https://semgrep.dev/explore
- GitHub Code Scanning (SARIF): https://docs.github.com/en/code-security/code-scanning
- Relacionados: [[ADR-0004]] (OIDC), [[ADR-0005]] (Pipeline CI/CD), [[ADR-0011]] (Kyverno admission)
