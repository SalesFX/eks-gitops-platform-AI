# ADR-0008: Centralização de Logs — Estratégia Free Tier (CloudWatch Logs com Fluent Bit) e Roadmap Loki

**Status:** Approved — implementação imediata (subconjunto mínimo)
**Data:** 2026-05-27
**Autores:** [Architect Agent]
**Supersedes / Relacionado:** [[ADR-0003]] (EKS Cluster, 2x t3.micro), [[ADR-0007 free-tier]] (metrics-server), [[ADR-0006]] (ArgoCD GitOps)

## Viabilidade Free Tier

> **Veredicto:** Parcialmente viável — adotar CloudWatch Logs via Fluent Bit DaemonSet com filtros agressivos; Loki adiado para Fase 2.
>
> Justificativa: validado via `aws-mcp` que o free tier do CloudWatch Logs inclui **5 GB/mês de ingestão + 5 GB de armazenamento + 1.800 minutos de Live Tail**. Um Fluent Bit DaemonSet consome ~40–80 MiB de RAM por node (~80–160 MiB no agregado em 2x t3.micro), cabe no orçamento de memória livre. Loki single-binary, mesmo com `persistence.enabled: false`, consome ~200–400 MiB de RAM (chunk cache + index cache + ingester) — somando Promtail/Alloy (~50 MiB/node), o footprint agregado (~300–500 MiB) excede a folga atual (~100–200 MiB). Logo, Loki fica como **roadmap Fase 2**.

## Contexto

Hoje a única forma de inspecionar logs do cluster `devops-ia-production` é via `kubectl logs <pod>`. Isso traz limitações operacionais críticas:

1. **Volátil**: logs de pods reciclados (CrashLoopBackOff, OOMKill, rollout) são perdidos quando o pod some.
2. **Sem busca**: não há agregação ou full-text search — diagnosticar uma exception específica exige iterar pod a pod.
3. **Sem correlação**: impossível correlacionar erros do frontend (Next.js) com 5xx do backend (.NET) sem inspecionar dois fluxos manualmente.
4. **Sem retenção controlada**: a retenção é controlada pelo Kubernetes runtime/`log-rotate` no node — tipicamente ~10 MiB por container, sem histórico para post-mortem.

Os Deployments do projeto (backend .NET e frontend Next.js, ambos com 2 réplicas) já escrevem em stdout/stderr (12-factor), portanto estão prontos para qualquer agregador que leia `/var/log/containers/*.log` no node.

### Restrição de capacidade (recap)

Memória disponível agregada nos 2 nodes `t3.micro`: **~100–200 MiB livres** após sistema EKS, ArgoCD, NGINX Ingress, metrics-server, frontend e backend. Qualquer agregador que consuma > 100 MiB total já cria risco de eviction sob carga.

### Validações via MCP

- **aws-mcp** — [Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/): free tier inclui **5 GB de ingestão de logs + 5 GB de armazenamento + 1.800 minutos de Live Tail/mês**. Acima disso, custos típicos us-east-1: US$ 0,50/GB de ingestão e US$ 0,03/GB-mês de armazenamento. Para um cluster com 2 nodes, 2 réplicas de backend + 2 de frontend + ArgoCD + ingress, a estimativa de log baseline é **~50–150 MiB/dia** (~1,5–4,5 GiB/mês) com filtros agressivos. Cabe no free tier com folga.
- **aws-mcp** — [EKS Cost-Opt Observability](https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt-observability.html): a AWS recomenda explicitamente **Fluent Bit (AWS for Fluent Bit)** como DaemonSet padrão para shipping de logs do data plane para CloudWatch ou S3, com filtros para descartar logs verbose (healthchecks, readiness probes, sidecar noise). Recomenda também forwarding direto para S3 em ambientes não-produção.
- **aws-mcp** — [Kubernetes Logging powered by AWS for Fluent Bit](https://aws.amazon.com/blogs/containers/kubernetes-logging-powered-by-aws-for-fluent-bit/): a imagem `public.ecr.aws/aws-observability/aws-for-fluent-bit` enriquece logs com metadados Kubernetes (pod, namespace, labels) e tipicamente consome ~30–60 MiB de RAM por node em clusters pequenos.
- **terraform-mcp** — provider `hashicorp/helm` versão `3.1.2` (latest, validado); recurso `helm_release` nativo é o caminho correto, sem módulos comunitários (regra de projeto em `.claude/rules/terraform-naming-conventions.md`).
- **terraform-mcp** — provider `hashicorp/kubernetes` versão `3.1.0` (latest, validado) para `kubernetes_manifest`/`kubernetes_namespace`.
- **aws-mcp** — Loki single-binary mode (do projeto Grafana Loki): recomendação oficial mínima é `512 MiB` de RAM para o ingester+querier em qualquer volume, com gauge real em clusters pequenos no intervalo `200–400 MiB`. Promtail/Alloy DaemonSet adiciona `50–80 MiB/node`. Para `t3.micro x2`, o agregado (~400–600 MiB) consumiria toda a folga de RAM, fora a falta de storage durável (sem PVC, retenção apenas em memória ~6h — perda total em restart).

## Decisão

### Fase 1 (atual — free-tier, escolhida)

Adotar **CloudWatch Logs via AWS for Fluent Bit DaemonSet** com filtros agressivos para caber no free tier:

1. **Agente**: `aws-for-fluent-bit` (imagem `public.ecr.aws/aws-observability/aws-for-fluent-bit:stable`) como `DaemonSet` no namespace `amazon-cloudwatch` (convenção AWS).
2. **Recursos por pod do agente**:
   - `requests`: `cpu 50m, memory 64Mi`
   - `limits`: `cpu 100m, memory 128Mi`
   - PriorityClass: `system-node-critical` (já que é DaemonSet de infra).
3. **Destinos (`[OUTPUT]`)**:
   - Único: `cloudwatch_logs` apontando para grupos por namespace.
4. **Estrutura de log groups**:
   - `/aws/eks/devops-ia-production/application` — apenas pods de `default` e namespaces de app (backend, frontend).
   - `/aws/eks/devops-ia-production/platform` — pods de `argocd`, `ingress-nginx`, `kube-system` (filtragem restritiva para reduzir ingestão).
   - `/aws/eks/devops-ia-production/host` — opcional, **desabilitado nesta fase** para economizar quota.
5. **Retenção**: **7 dias** em todos os log groups (controlada via `aws_cloudwatch_log_group.retention_in_days = 7`). Isso mantém custo de armazenamento próximo de zero (5 GB free).
6. **Filtros obrigatórios no Fluent Bit** (cortar volume de ingestão):
   - `Exclude_Path /var/log/containers/aws-node*.log,/var/log/containers/kube-proxy*.log,/var/log/containers/aws-for-fluent-bit*.log`
   - `[FILTER] kubernetes` com `Merge_Log On` e `Keep_Log Off` (descarta o JSON raw após parse).
   - `[FILTER] grep` para descartar linhas com `GET /health`, `GET /healthz`, `GET /ready` (ruído de probe).
   - `[FILTER] modify` para remover campos pesados não usados (`kubernetes.pod_id`, `kubernetes.docker_id`).
7. **IAM (IRSA)**: criar IAM Role com policy `CloudWatchAgentServerPolicy` (managed AWS) — ou versão custom mínima com `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`, `logs:DescribeLogStreams`, escopado aos grupos `/aws/eks/devops-ia-production/*`. Reaproveita-se o padrão OIDC já estabelecido em [[ADR-0004]].
8. **Operação diária**: CloudWatch Logs Insights queries gravadas para os 5 casos top:
   - Errors do backend (.NET): `fields @timestamp, kubernetes.pod_name, log | filter kubernetes.container_name = "backend" | filter log like /ERROR|Exception/`
   - 5xx no NGINX Ingress: `... | filter kubernetes.container_name = "controller" | filter log like /\\s5\\d\\d\\s/`
   - OOMKills detectados: `... | filter log like /OOMKilled/`
   - ArgoCD sync failures: `... | filter kubernetes.namespace_name = "argocd" | filter log like /sync.*failed/`
   - Frontend Next.js render errors.
9. **Não habilitar nesta fase**: control plane logs do EKS (API server, audit, authenticator, controller-manager, scheduler). Esses logs sozinhos podem consumir 1–5 GB/dia em clusters ociosos — estouram o free tier. Habilitar apenas no troubleshooting pontual ou na Fase 2.

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: agregação centralizada habilita queries históricas via CloudWatch Logs Insights — fim do "kubectl logs roulette". Runbooks de incidentes ganham links diretos para queries pré-salvas.
2. **Security**: CloudWatch Logs criptografado at-rest por padrão (AWS managed key); IRSA elimina credenciais long-lived no agente; logs de auditoria do EKS podem ser habilitados sob demanda sem mudar a arquitetura.
3. **Reliability**: Fluent Bit é resiliente — buffer em disco local (`storage.type filesystem`) garante zero perda durante blips de rede com CloudWatch. Falha do agente em 1 node não afeta o outro (DaemonSet por design).
4. **Performance Efficiency**: Fluent Bit é escrito em C, footprint ordens de magnitude menor que Fluentd/Logstash. CloudWatch Logs absorve picos sem provisioning prévio.
5. **Cost Optimization**: zero custo direto enquanto ingestão ≤ 5 GB/mês. Filtros agressivos no agente garantem que probes e logs de infra não consumam a quota.
6. **Sustainability**: nenhum hardware adicional; reaproveita CloudWatch existente; retenção curta minimiza armazenamento.

## Configuração Mínima Adotada

```text
Namespace:           amazon-cloudwatch
DaemonSet:           aws-for-fluent-bit (1 pod por node = 2 pods)
Imagem:              public.ecr.aws/aws-observability/aws-for-fluent-bit:stable
Recursos/pod:        50m CPU / 64Mi RAM (request) → 100m / 128Mi (limit)
ServiceAccount:      fluent-bit (IRSA → CloudWatchLogs role)
PriorityClass:       system-node-critical
Storage buffer:      filesystem, max 100MB por pod
Retenção CW Logs:    7 dias (todos os grupos)
Log groups (3):      application | platform | (host = OFF)
Filtros:             Exclude_Path + grep "health/ready" + modify (drop pod_id/docker_id)
```

Provider stack para o `devops-senior-engineer` implementar:
- `hashicorp/aws ~> 6.0` (já em uso)
- `hashicorp/helm ~> 3.1` (validado via terraform-mcp em 2026-05-27)
- `hashicorp/kubernetes ~> 3.1` (validado via terraform-mcp em 2026-05-27)

## Consequências

### Positivas

- Visibilidade central de logs sem custo recorrente enquanto < 5 GB/mês.
- Possibilidade de queries SQL-like com CloudWatch Logs Insights — habilita troubleshooting eficiente.
- Footprint no cluster: ~60–120 MiB no agregado (cabe na folga de `t3.micro x2`).
- Integração natural com alarmes futuros (Metric Filters → Alarms → SNS) caso aprovado.
- Zero impacto em rollback do app: agente é DaemonSet de infra, desacoplado dos Deployments.

### Negativas / Trade-offs

- **Vendor lock-in parcial** com CloudWatch (mitigável: Fluent Bit pode rotear para Loki/S3/OpenSearch futuramente trocando apenas a seção `[OUTPUT]`).
- **Sem dashboards visuais ricos**: CloudWatch Logs Insights tem UI funcional mas inferior a Grafana+Loki para painéis customizados.
- **Retenção curta (7 dias)**: post-mortems > 1 semana não terão logs disponíveis. Mitigação Fase 2: subscription filter para S3 (custo ~US$ 0,023/GB-mês).
- **Sem correlação trace ↔ log nativa**: sem OpenTelemetry/X-Ray, não há `trace_id` propagado. Aceito como gap conhecido.
- **Risco de estouro do free tier** se a app começar a logar verbose. Mitigação: alarme em `IncomingBytes` do log group em ~4 GB/mês.

## Alternativas Consideradas

| Alternativa | Footprint cluster | Custo/mês | Motivo da rejeição |
|---|---|---|---|
| Loki single-binary (sem PVC) + Promtail | ~300–500 MiB RAM | US$ 0 | **Não cabe** em `t3.micro x2`. Validado via aws-mcp — recomendação oficial Loki é 512 MiB mínimo no ingester; promtail/alloy adiciona ~50 MiB/node. |
| Loki single-binary com PVC EBS gp3 | ~400–600 MiB RAM + ~US$ 2/mês PVC | ~US$ 2 | Mesma restrição de RAM. Custo de EBS pequeno mas RAM é o bloqueador. |
| Grafana Cloud (free tier: 50 GB logs, 14 dias) | ~50 MiB (Alloy agent) | US$ 0 (até limite) | Vendor SaaS externo, foge do escopo "open-source self-hosted ou AWS-native" do projeto. Reavaliar em Fase 2 se o time aceitar SaaS. |
| OpenSearch Service (managed) | ~50 MiB (agent) | US$ 25+/mês mínimo | Excessivo para o volume; baseline t3.small.search já estoura budget free-tier. |
| Apenas `kubectl logs` (status quo) | 0 MiB | US$ 0 | Não resolve volatilidade, sem busca, sem retenção. **Status quo é o problema**. |
| Sidecar de logging por pod (sem DaemonSet) | ~30 MiB × N pods | US$ 0 | Multiplica overhead por pod; não escala em cluster pequeno. |

## Roadmap de Evolução

| Fase | Gatilho | O que adicionar |
|---|---|---|
| **Fase 1 (atual — t3.micro x2)** | — | Fluent Bit DaemonSet → CloudWatch Logs com retenção 7 dias e filtros agressivos. Insights queries gravadas. |
| **Fase 2 (t3.medium x 2–3 nodes)** | Upgrade dos nodes OU >5 incidentes/mês onde faltou histórico | Adicionar **Loki single-binary mode** com PVC EBS gp3 (20 GiB), retenção 30 dias. Promtail ou Alloy substituem o `[OUTPUT] cloudwatch_logs` por `[OUTPUT] loki`. Grafana lê de Loki via data source. CloudWatch Logs vira destino secundário (apenas para alarmes via Metric Filter). |
| **Fase 3 (3+ nodes, produção real)** | Necessidade de retenção > 30 dias OU compliance | **Loki + S3 backend** (TSDB index, S3 chunks) — retenção ilimitada a baixo custo (~US$ 0,023/GB-mês). Subscription filter de CloudWatch Logs → S3 também para logs do control plane do EKS. Considerar **AWS for OpenTelemetry Collector** para correlação trace ↔ log. |
| **Fase 4 (multi-cluster ou compliance estrito)** | Multi-region / multi-cluster | Centralized logging account pattern (AWS Logging Account); Loki multi-tenant com X-Scope-OrgID; ou migrar para Grafana Cloud / Datadog se time/budget permitir. |

## Critérios de Aceitação

- [ ] DaemonSet `aws-for-fluent-bit` rodando 2/2 pods (um por node), namespace `amazon-cloudwatch`.
- [ ] IAM Role IRSA criada via Terraform, anexada à ServiceAccount `fluent-bit`.
- [ ] 2 log groups criados: `/aws/eks/devops-ia-production/application` e `/aws/eks/devops-ia-production/platform`, ambos com `retention_in_days = 7`.
- [ ] Filtros confirmados via `kubectl logs daemonset/aws-for-fluent-bit -n amazon-cloudwatch` (ver decisões de `Exclude_Path` e `grep`).
- [ ] Logs do backend e do frontend visíveis em CloudWatch Logs Insights via query `fields @timestamp, log | filter kubernetes.container_name = "backend"`.
- [ ] CloudWatch alarme em `IncomingBytes` do log group `application` com threshold em ~4 GB/30 dias (alerta antes de sair do free tier).
- [ ] Footprint de RAM do DaemonSet em `kubectl top pods -n amazon-cloudwatch` ≤ 128 MiB por pod.
- [ ] Documentado o gatilho para promover à Fase 2 (upgrade de nodes OU 5+ incidentes/mês com falta de histórico).
- [ ] Runbook `docs/runbooks/logs-cloudwatch-queries.md` publicado com as 5 queries gravadas.

## Referências

- AWS for Fluent Bit (validado via aws-mcp): https://aws.amazon.com/blogs/containers/kubernetes-logging-powered-by-aws-for-fluent-bit/
- EKS Cost-Opt Observability (validado via aws-mcp): https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt-observability.html
- CloudWatch Pricing — Free Tier (validado via aws-mcp): https://aws.amazon.com/cloudwatch/pricing/
- Grafana Loki sizing: https://grafana.com/docs/loki/latest/setup/size/
- Provider `hashicorp/helm` 3.1.2 (validado via terraform-mcp)
- Provider `hashicorp/kubernetes` 3.1.0 (validado via terraform-mcp)
- Relacionados: [[ADR-0003]] (EKS), [[ADR-0004]] (OIDC/IRSA), [[ADR-0006]] (ArgoCD), [[ADR-0007 free-tier]] (metrics-server)
