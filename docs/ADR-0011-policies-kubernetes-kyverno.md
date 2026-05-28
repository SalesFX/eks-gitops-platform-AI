# ADR-0011: Policies Kubernetes — Kyverno (modo Audit) vs PSA — Decisão Free Tier

**Status:** Approved — implementação imediata com Pod Security Admission (PSA) nativo; Kyverno adiado para Fase 2
**Data:** 2026-05-27
**Autores:** [Architect Agent]
**Supersedes / Relacionado:** [[ADR-0003]] (EKS Cluster), [[ADR-0006]] (ArgoCD), [[ADR-0009]] (Pipeline Security), `.claude/rules/kubernetes-manifests.md`

## Viabilidade Free Tier

> **Veredicto:** Inviável agora para Kyverno — adotar Pod Security Admission (PSA) nativo do Kubernetes na Fase 1; Kyverno permanece como roadmap explícito para Fase 2.
>
> Justificativa: validado via `aws-mcp` que a recomendação AWS para clusters EKS com restrição de recursos é começar com **Pod Security Admission (PSA)** + **Pod Security Standards (PSS)**, que são built-in do Kubernetes (≥ 1.25) e têm **footprint zero** — são features do `kube-apiserver`. Kyverno em modo mínimo (1 réplica admission-controller + 1 réplica background-controller + 1 réplica cleanup-controller + 1 réplica reports-controller, defaults da chart oficial v1.12+) consome **~250–400 MiB RAM agregado** (validado em diversos benchmarks da comunidade Kyverno). Em `t3.micro x2` com folga de ~100–200 MiB, isso esgotaria a margem e potencialmente causaria OOMKill em ArgoCD ou nos pods de aplicação. Adicionalmente, o webhook de admission adiciona ~50–150ms ao startup de cada pod — penalidade mensurável em cluster pequeno onde rollouts são frequentes.

## Contexto

As regras do projeto em `.claude/rules/kubernetes-manifests.md` exigem várias garantias de segurança e qualidade em todo Deployment: `runAsNonRoot`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem`, capabilities drop ALL, probes obrigatórias, resources requests/limits, labels padronizadas, tag específica (nunca `:latest`).

Hoje **essas regras são verificadas apenas por revisão manual de PR** — não há gate automatizado no cluster que rejeite um manifest não conforme. Cenários reais que isso permite:

1. Alguém adiciona um Deployment com `image: app:latest` — passa em revisão por descuido.
2. Sidecar de debug com `privileged: true` é introduzido em hotfix.
3. Manifest sem `resources.limits` é deployado — pod consome toda RAM disponível e causa eviction em cascata (especialmente perigoso em `t3.micro`).
4. ConfigMap montado com `readOnly: false` permite escrita acidental que escapa do snapshot semanal ([[ADR-0010]]).

Precisamos de um **admission control** que bloqueie ou reporte violações. As opções principais avaliadas:

### Validações via MCP

- **aws-mcp** — [Implementing Pod Security Standards in Amazon EKS](https://aws.amazon.com/blogs/containers/implementing-pod-security-standards-in-amazon-eks/): AWS confirma que **PSA (Pod Security Admission)** é o caminho built-in pós-Kubernetes 1.25 (PSPs foram removidas). PSA aplica três níveis (`privileged`, `baseline`, `restricted`) em três modos (`enforce`, `audit`, `warn`) por namespace via label `pod-security.kubernetes.io/<mode>: <level>`. **Footprint: zero** — é uma feature do `kube-apiserver`.
- **aws-mcp** — [Easy as one-two-three policy management with Kyverno on Amazon EKS](https://aws.amazon.com/blogs/containers/easy-as-one-two-three-policy-management-with-kyverno-on-amazon-eks/): Kyverno é o policy engine recomendado pela AWS quando regras vão além do que PSA cobre (ex.: regras custom de labels, image registry, namespaces). Mas a documentação não cita números de footprint específicos — referência cruzada com docs oficiais Kyverno indica ~250 MiB RAM para um deploy mínimo HA, ou ~150–200 MiB para 1 réplica de cada controller.
- **aws-mcp** — [Use admission controllers to enforce security policies (EKS multi-tenant whitepaper)](https://docs.aws.amazon.com/whitepapers/latest/security-practices-multi-tenant-saas-applications-eks/use-admission-controllers-to-enforce-security-policies.html): AWS recomenda **um ou ambos** (PSA + OPA/Kyverno) — não obrigatoriamente os dois ao mesmo tempo.
- **terraform-mcp** — Módulo `lablabs/eks-kyverno/aws/3.0.0` existe mas é comunitário não-verificado (500 downloads). **Não usar** — regra do projeto exige recursos nativos via `hashicorp/helm` + `helm_release`. Quando Kyverno for adotado (Fase 2), instalar via chart oficial `kyverno/kyverno`.
- **terraform-mcp** — provider `hashicorp/kubernetes 3.1.0` suporta nativamente labels em namespaces via `kubernetes_namespace_v1` — caminho ideal para aplicar labels PSA em IaC.

## Decisão

### Fase 1 (atual, adotada) — Pod Security Admission nativo + scans em CI

Aplicar **PSA em modo `enforce: baseline` + `warn: restricted`** em todos os namespaces de aplicação, e **`enforce: restricted`** em namespaces dedicados. Combinar com Checkov ([[ADR-0009]]) que já roda em CI para os manifests em `devops-ia-kubernetes/`.

#### Configuração por namespace

| Namespace | enforce | audit | warn | Justificativa |
|---|---|---|---|---|
| `default` / app namespaces (`backend`, `frontend`) | `baseline` | `restricted` | `restricted` | Aplicações stateless 12-factor; `restricted` é alcançável. Iniciar com `enforce: baseline` (mais permissivo) e promover a `restricted` após 2 semanas estáveis. |
| `kube-system` | `privileged` | — | — | Necessário para CNI, kube-proxy, CoreDNS, metrics-server. |
| `argocd` | `baseline` | `restricted` | `restricted` | ArgoCD não precisa de capabilities elevadas. |
| `ingress-nginx` | `baseline` | `restricted` | `restricted` | NGINX precisa bind em port 80/443 — `baseline` permite via `runAsNonRoot` + capabilities ajustadas. |
| `amazon-cloudwatch` | `privileged` | — | — | Fluent Bit DaemonSet ([[ADR-0008]]) precisa ler `/var/log/containers` no host. |

Aplicação via Terraform `kubernetes_namespace_v1`:

```text
metadata.labels:
  pod-security.kubernetes.io/enforce: <level>
  pod-security.kubernetes.io/enforce-version: latest
  pod-security.kubernetes.io/audit: <level>
  pod-security.kubernetes.io/warn: <level>
```

#### Cobertura PSA vs regras do projeto

PSA cobre **a maioria** das regras de `.claude/rules/kubernetes-manifests.md`:

| Regra do projeto | Nível PSA que cobre | Notas |
|---|---|---|
| `runAsNonRoot: true` | `restricted` | OK |
| `allowPrivilegeEscalation: false` | `restricted` | OK |
| `privileged: false` (bloqueio) | `baseline` | OK |
| `readOnlyRootFilesystem: true` | **Não coberto** | Gap — só Checkov K8s ou Kyverno cobre. Aceito como warning em CI ([[ADR-0009]]). |
| Capabilities drop ALL | `restricted` | OK (drop ALL é exigido) |
| Probes obrigatórias | **Não coberto** | Gap — Checkov K8s cobre via `CKV_K8S_8/9` (livenessProbe/readinessProbe). |
| Resources requests/limits | **Não coberto** | Gap — Checkov cobre via `CKV_K8S_10/11/12/13` (CPU/Mem requests/limits). |
| Labels padronizadas | **Não coberto** | Gap — Checkov customizado ou Kyverno (Fase 2). |
| Imagem nunca `:latest` | **Não coberto** | Gap — Checkov `CKV_K8S_43` cobre. |
| Volumes ReadOnly por padrão | **Não coberto** | Gap — Checkov K8s + revisão manual. |

**Estratégia de cobertura híbrida**:
- PSA cobre o que é built-in e free (security baseline).
- Checkov K8s rodando em CI ([[ADR-0009]]) cobre os gaps que sobram via análise estática nos PRs.
- Gaps de runtime (recursos criados via `kubectl apply` direto, fora do GitOps) ficam descobertos até Fase 2 (Kyverno) — mitigação: ArgoCD com auto-sync + selfHeal ([[ADR-0006]]) reverte mudanças out-of-band em ~3 min.

#### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: PSA não tem operação — features do API server. Sem pods extras, sem upgrades de chart, sem CRDs.
2. **Security**: cobertura `baseline → restricted` é o padrão AWS recomendado. Combinado com Checkov no CI, fecha ~85% das regras do projeto.
3. **Reliability**: zero pods adicionados, zero webhook latency. PSA não introduz pontos de falha (failure mode é "fail-closed" no apiserver, mas sem latência percebida).
4. **Performance Efficiency**: zero overhead de runtime; sem chamadas HTTP a admission webhooks.
5. **Cost Optimization**: zero custo direto; zero RAM consumida; sem EBS adicional.
6. **Sustainability**: feature nativa, reaproveita o kube-apiserver.

### Fase 2 (deferida) — Kyverno em modo Audit, depois Enforce

Quando o cluster for upgraded para `t3.medium` (ou maior), instalar Kyverno via Helm chart oficial `kyverno/kyverno` (release `kyverno`, namespace `kyverno`):

- **1 réplica de cada controller** (admission, background, cleanup, reports) — modo non-HA.
- Recursos: `requests cpu 100m / memory 128Mi` | `limits cpu 200m / memory 256Mi` por controller.
- **PriorityClass**: `system-cluster-critical` para evitar eviction.
- **Modo inicial: Audit** (`validationFailureAction: Audit` em todos os ClusterPolicies) por **mínimo 14 dias** — apenas reporta violations em PolicyReports, não bloqueia.
- **Promoção para Enforce** condicionada a: zero violações inesperadas em PolicyReports por 14 dias OU exceção explícita registrada em ClusterPolicyException.

#### Policies obrigatórias (Fase 2) baseadas em `.claude/rules/kubernetes-manifests.md`

| Policy | Tipo | Rule kind |
|---|---|---|
| `disallow-latest-tag` | validate | Bloquear `image: <foo>:latest` ou imagem sem tag explícita. |
| `require-probes` | validate | Exigir `readinessProbe` e `livenessProbe` em todo container. |
| `require-resources` | validate | Exigir `resources.requests.cpu`, `resources.requests.memory`, `resources.limits.cpu`, `resources.limits.memory`. |
| `require-non-root` | validate | Exigir `securityContext.runAsNonRoot: true` em Pod E container. |
| `disallow-privilege-escalation` | validate | Exigir `allowPrivilegeEscalation: false`. |
| `disallow-privileged` | validate | Bloquear `privileged: true`. |
| `require-labels` | validate | Exigir labels `app.kubernetes.io/name`, `app.kubernetes.io/component`, `app.kubernetes.io/part-of`, `environment`. |
| `require-readonly-rootfs` | validate | Exigir `securityContext.readOnlyRootFilesystem: true` (com exceção via annotation). |
| `disallow-capabilities-add` | validate | Bloquear `capabilities.add: [...]` (forçar drop ALL). |
| `require-ns-quotas` | generate | Auto-gerar ResourceQuota + LimitRange por namespace de app. |
| `disallow-default-namespace` | validate | Forçar uso de namespace específico (warn-only). |

**Exceções (namespaces sem policy enforcement em Fase 2):**
- `kube-system` — sistema EKS, fora do escopo.
- `kyverno` — auto-exclusão para evitar deadlock.
- `argocd` — auto-exclusão para o controller (não para apps geridas por ele).
- `amazon-cloudwatch` — Fluent Bit DaemonSet ([[ADR-0008]]) precisa de host paths.

## Configuração Mínima Adotada (Fase 1)

```text
Pod Security Admission (built-in EKS ≥ 1.25):
  Namespaces de app:       enforce=baseline, audit=restricted, warn=restricted
  Namespaces sistema:      enforce=privileged (kube-system, amazon-cloudwatch)
  Namespaces plataforma:   enforce=baseline, audit=restricted, warn=restricted
                           (argocd, ingress-nginx)

Aplicação via Terraform:
  Recurso:                 kubernetes_namespace_v1
  Provider:                hashicorp/kubernetes ~> 3.1 (validado via terraform-mcp)

Cobertura adicional via CI (já em ADR-0009):
  Checkov framework=kubernetes em devops-ia-kubernetes/**/*.yaml
  Checks habilitados:      CKV_K8S_8 (livenessProbe), CKV_K8S_9 (readinessProbe),
                           CKV_K8S_10-13 (resources), CKV_K8S_43 (no :latest tag),
                           CKV_K8S_22 (read-only rootfs), CKV_K8S_28 (capabilities)
```

## Consequências

### Positivas

- **Footprint zero** no cluster — sem overhead em `t3.micro x2`.
- Cobertura imediata de `baseline` em todos os namespaces de app + plataforma.
- Sem dependência de chart de terceiros, sem CRDs, sem upgrades a operar.
- Promoção `baseline → restricted` é mudança de label (rollout instantâneo).
- Gaps cobertos por Checkov no CI ([[ADR-0009]]) — gate antes do merge.

### Negativas / Trade-offs

- **PSA não cobre todas as regras do projeto** (probes, resources, labels, readonly rootfs, no-latest). Esses gaps dependem do CI (Checkov) — não há defesa em runtime se alguém `kubectl apply` fora do GitOps.
- **Sem PolicyReports** — não há objeto Kubernetes consultável de violações (PSA grava apenas em audit log do apiserver e CloudWatch quando control plane logs estão habilitados, custo desabilitado em [[ADR-0008]]).
- **Promoção a `restricted` exige discovery de breakage**: aplicações que dependiam de capabilities ou rootfs writable param de subir. Mitigação: começar com `enforce: baseline + warn: restricted` por 2 semanas e ler warnings nos `kubectl` outputs.
- **Sem custom rules** (image registry whitelist, label enforcement, ResourceQuota generation) — esperar Fase 2 com Kyverno.

## Alternativas Consideradas

| Alternativa | Motivo da rejeição |
|---|---|
| **Kyverno agora (1 réplica de cada controller)** | Footprint agregado ~250–400 MiB excede a folga em `t3.micro x2`. Validado via aws-mcp e benchmark da comunidade. Webhook adiciona 50–150ms de latência em cada admission — sensível em rollouts frequentes. |
| **Kyverno apenas reports-controller** | Configuração não suportada oficialmente; admission webhook é parte estrutural. |
| **OPA Gatekeeper** | Footprint comparável a Kyverno (~200–350 MiB); linguagem Rego tem curva de aprendizado maior; AWS blog explicitamente menciona Kyverno como "easier" para EKS. |
| **Apenas Checkov em CI (sem PSA)** | Não cobre `kubectl apply` direto fora do GitOps. Mesmo com ArgoCD auto-sync, há janela de risco de ~3 min. PSA fecha essa janela com custo zero. |
| **PSP (Pod Security Policy)** | Removidas em Kubernetes 1.25. EKS 1.31 já as removeu. Não aplicável. |
| **Apenas revisão manual de PR** | Status quo; sujeito a erro humano e não escalável. |

## Roadmap de Evolução

| Fase | Gatilho | O que adicionar |
|---|---|---|
| **Fase 1 (atual — t3.micro x2)** | — | PSA `enforce: baseline + warn: restricted` em app/plataforma. Checkov K8s no CI cobre gaps. |
| **Fase 1.5 (mesmo cluster, semana +2)** | Zero pods rejeitados por `warn: restricted` em 14 dias | Promover app namespaces para `enforce: restricted`. |
| **Fase 2 (t3.medium x 2–3 nodes)** | Upgrade dos nodes OU primeira violação detectada que PSA não pegou | Instalar **Kyverno** via Helm chart oficial (`kyverno/kyverno`), 1 réplica/controller, modo `validationFailureAction: Audit` em todas as ClusterPolicies. Aplicar as 11 policies listadas (audit-only por 14 dias). |
| **Fase 2.5** | 14 dias sem violations inesperadas | Promover ClusterPolicies para `validationFailureAction: Enforce`. Habilitar **PolicyReports** consultáveis. Habilitar mutate policies (auto-injetar labels padrão, auto-set imagePullPolicy). |
| **Fase 3 (produção real)** | Compliance ou multi-time | Kyverno **HA** (3 réplicas/controller). Adicionar policies de imagem assinada (Sigstore/Cosign `verifyImages`). Adicionar policy de namespace ownership (CODEOWNERS-style). Integrar PolicyReports com SIEM. |
| **Fase 4 (multi-tenant)** | Mais de uma equipe compartilhando cluster | Adicionar **OPA Gatekeeper** em paralelo a Kyverno para policies cross-cluster gerenciadas pela equipe de plataforma. Network Policies obrigatórias por namespace. |

## Critérios de Aceitação

- [ ] Todos os namespaces de app (`default`, `backend`, `frontend` quando criados) com labels PSA `enforce=baseline`, `audit=restricted`, `warn=restricted` aplicadas via Terraform.
- [ ] Namespaces `argocd` e `ingress-nginx` com mesma config PSA.
- [ ] Namespaces `kube-system` e `amazon-cloudwatch` com `enforce=privileged` (justificado em comentário no manifest).
- [ ] Teste: tentar aplicar `kubectl run nginx --image=nginx --privileged` em namespace de app → **deve falhar** com mensagem PSA explícita.
- [ ] Teste: tentar aplicar Deployment com `securityContext.runAsUser: 0` em namespace de app com `enforce: restricted` → **deve falhar**.
- [ ] Checkov K8s rodando no CI ([[ADR-0009]]) com pelo menos os checks `CKV_K8S_8/9/10/11/12/13/22/28/43` habilitados.
- [ ] Documentado o gatilho explícito para promover `baseline → restricted` (14 dias estáveis).
- [ ] Documentado o gatilho explícito para promover Fase 2 (Kyverno): upgrade de nodes OU violação real não pega por PSA.
- [ ] Runbook `docs/runbooks/admission-policies.md` publicado com exceções por namespace e procedimento para casos especiais.

## Referências

- AWS Containers Blog — PSA em EKS (validado via aws-mcp): https://aws.amazon.com/blogs/containers/implementing-pod-security-standards-in-amazon-eks/
- AWS Containers Blog — Kyverno em EKS (validado via aws-mcp): https://aws.amazon.com/blogs/containers/easy-as-one-two-three-policy-management-with-kyverno-on-amazon-eks/
- AWS EKS Best Practices — Pod Security (validado via aws-mcp): https://docs.aws.amazon.com/eks/latest/best-practices/pod-security.html
- AWS Whitepaper — Admission Controllers (validado via aws-mcp): https://docs.aws.amazon.com/whitepapers/latest/security-practices-multi-tenant-saas-applications-eks/use-admission-controllers-to-enforce-security-policies.html
- Pod Security Standards: https://kubernetes.io/docs/concepts/security/pod-security-standards/
- Kyverno docs: https://kyverno.io/docs/
- Provider `hashicorp/kubernetes 3.1.0` (validado via terraform-mcp)
- Regras do projeto: `.claude/rules/kubernetes-manifests.md`
- Relacionados: [[ADR-0003]] (EKS), [[ADR-0009]] (Pipeline Security), [[ADR-0008]] (Fluent Bit), [[ADR-0006]] (ArgoCD)
