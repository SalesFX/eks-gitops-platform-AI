# ADR-0007: Observabilidade Free Tier — metrics-server e evolução futura

**Status:** Proposed
**Data:** 2026-05-27
**Autores:** [Architect Agent]

> Nota de relação com outras ADRs: este documento substitui (supersedes) o ADR anterior `ADR-0007-observabilidade-kube-prometheus-stack.md`, que ainda estava em status `Proposed` e assumia node group `t3.medium x2`. Uma vez que o cluster real foi provisionado com `t3.micro x2`, a decisão original tornou-se inexequível e precisa ser substituída. O ADR antigo deve ser marcado como `Superseded by ADR-0007 (free-tier)` ao ser revisitado.

## Contexto

A plataforma EKS `devops-ia-production` (us-east-1, conforme ADR-0003) precisa de observabilidade mínima para tornar visível o consumo de CPU/memória dos pods e do cluster. Isso é necessário tanto para operação diária quanto para o objetivo pedagógico/portfolio do projeto.

### Restrição crítica de capacidade

O cluster está provisionado com **2 nodes `t3.micro`** (1 vCPU, 1 GiB RAM cada). Após o overhead obrigatório de cada node, sobra muito pouca capacidade utilizável:

| Componente já residente | Memória aproximada |
|---|---|
| Sistema EKS (kubelet, kube-proxy, aws-node/VPC CNI, CoreDNS) | ~250–350 MiB por node |
| Reserva do kubelet (`kube-reserved`, `system-reserved`, `eviction-hard`) | ~100–200 MiB por node |
| ArgoCD (ADR-0006) | ~250–400 MiB no agregado |
| Ingress Controller (nginx ou AWS LBC) | ~100–200 MiB no agregado |
| Frontend Next.js + Backend .NET (2 réplicas cada) | ~300–500 MiB no agregado |

Disponível para novas cargas operacionais: estimado em **~100–250 MiB de RAM livre no agregado dos 2 nodes**. Subir `kube-prometheus-stack` (Prometheus + Alertmanager + Grafana + node-exporter + kube-state-metrics + Operator), que consome ~1.0–1.4 GiB no agregado, causaria **OOMKill em cadeia** e instabilidade dos pods de aplicação — confirmado pelos requisitos publicados pela comunidade Prometheus e pela orientação da AWS sobre node efficiency.

### Validações via MCP

- **aws-mcp**: A documentação oficial AWS ([EKS — Kubernetes Metrics Server](https://docs.aws.amazon.com/eks/latest/userguide/metrics-server.html)) confirma que o `metrics-server` é o caminho recomendado pela AWS para métricas point-in-time de CPU/memória em qualquer cluster EKS, sendo inclusive disponibilizado como **community add-on gerenciado** pela EKS desde 2024. A própria AWS adverte que `metrics-server` "não é uma solução de monitoramento ou análise histórica" — para isso, a recomendação é Container Insights ou Prometheus.
- **aws-mcp**: A página de pricing do CloudWatch ([Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)) confirma que **CloudWatch Container Insights NÃO é incluído no free tier** — é cobrado por métrica custom ingerida e por GB de logs além dos 5 GB/mês gratuitos. Para um cluster com 2 nodes e ~10 pods, o custo estimado é US$ 5–15/mês mesmo no menor modo, o que descarta Container Insights na fase free-tier. O free tier do CloudWatch (5 GB de logs, 10 métricas custom, 10 alarmes, 3 dashboards) é útil apenas para alertas pontuais de infra.
- **terraform-mcp**: O módulo público mais próximo (`boeboe/metrics-server/helm 0.0.1`) é não-verificado, pouco baixado (119 downloads) e está pinned em `helm provider ~> 2.7.1`. **Recomendação: não usar módulo comunitário**; instalar via Helm chart oficial `metrics-server` do repo `https://kubernetes-sigs.github.io/metrics-server` diretamente com o recurso nativo `helm_release` (provider `hashicorp/helm`), seguindo a regra do projeto de não usar módulos comunitários (`.claude/rules/terraform-naming-conventions.md`).
- **aws-mcp**: O `metrics-server` em sua configuração padrão consome tipicamente **~50–100 milliCPU e ~30–80 MiB de RAM** por réplica em clusters pequenos — perfeitamente comportável nos `t3.micro` mesmo com `replicas: 1`. Para HA, 2 réplicas podem ser usadas se a capacidade permitir.

## Decisão

### Fase 1 (atual, free-tier-first)

1. **Instalar apenas o `metrics-server`** no cluster, no namespace `kube-system` (convenção exigida para que o `kubectl top` e o HPA funcionem out-of-the-box).
   - Fonte: Helm chart oficial `metrics-server` (`https://kubernetes-sigs.github.io/metrics-server`).
   - Réplicas: `1` (recurso escasso; o cluster MVP aceita gap de até 60s sem métrica durante restart).
   - Resources: `requests: cpu 50m, memory 64Mi` | `limits: cpu 100m, memory 128Mi`.
   - PriorityClass: `system-cluster-critical` para evitar eviction em caso de pressão de memória.
   - Args: `--kubelet-insecure-tls` apenas se o kubelet do node group não tiver certificado assinado pela CA do cluster (validar em apply); por padrão **não habilitar**, manter TLS estrito.
2. **Operação diária via `kubectl top`**:
   - `kubectl top nodes`
   - `kubectl top pods -A`
   - `kubectl top pods -A --sort-by=memory`
3. **NÃO instalar nesta fase**: Prometheus, Alertmanager, Grafana, Loki, Thanos, Mimir, kube-state-metrics, node-exporter, Tempo, Jaeger.
4. **NÃO ativar CloudWatch Container Insights nesta fase** (custo recorrente acima do free-tier, conforme validação MCP).
5. **Uso do CloudWatch free tier exclusivamente para alarmes mínimos de infra** (limitar-se a até 10 alarmes incluídos no free tier):
   - 1 alarme: `NodeStatusCondition` Ready=False (via custom metric ou via EventBridge → SNS).
   - 1 alarme: CPU EC2 do node group > 85% por 15min (métrica EC2 nativa, sem custo).
   - 1 alarme: Memória EC2 do node group > 85% por 15min — **requer agente** (descartado nesta fase por sobrecarregar o node; usar apenas o limite de pressão do kubelet via `kubectl describe node`).
6. **Documentar limitações** e operar com alertas manuais por hora/dia via runbook (ver `docs/runbooks/observability-free-tier.md`).
7. **Pré-requisito de execução**: todos os Deployments do projeto devem ter `resources.requests` e `resources.limits` definidos (regra do projeto em `.claude/rules/kubernetes-manifests.md`). Sem isso, `kubectl top pods` retorna o consumo, mas a interpretação fica prejudicada (não há denominador para comparar).

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: `metrics-server` é install-and-forget, gerenciado como community add-on EKS; expõe a Metrics API padrão usada por `kubectl top` e HPA. Sem operação adicional.
2. **Security**: Componente único, namespace `kube-system`, RBAC mínimo já definido pelo chart oficial. Nenhum endpoint exposto externamente. TLS estrito com o kubelet sempre que possível.
3. **Reliability**: Réplica única é trade-off consciente; em caso de OOM no `metrics-server`, o cluster e as aplicações continuam funcionando (impacta apenas HPA e `kubectl top`). PriorityClass `system-cluster-critical` reduz risco de eviction.
4. **Performance Efficiency**: `metrics-server` agrega métricas direto do kubelet/cAdvisor sem armazenamento — overhead mínimo. Suficiente para o volume e SLA do MVP.
5. **Cost Optimization**: Custo direto **zero**. Não usa CloudWatch Container Insights (que sairia do free tier), não usa Managed Prometheus (US$ ~40+/mês baseline), não usa Datadog/New Relic.
6. **Sustainability**: Footprint mínimo de CPU/RAM; sem armazenamento de séries históricas; reaproveita a coleta nativa do kubelet.

## Consequências

### Positivas

- Cluster passa a expor métricas point-in-time de CPU/memória para `kubectl top` e HPA imediatamente.
- Custo direto **US$ 0,00** adicional na fatura AWS.
- Footprint de ~50–100 MiB no cluster (cabe folgadamente em `t3.micro x2`).
- Habilita Horizontal Pod Autoscaler caso seja útil no futuro próximo.
- Sem dívida operacional: o componente é maduro, mantido pela `kubernetes-sigs`, e é o caminho oficial sugerido pela AWS.
- Roadmap claro para escalar a observabilidade conforme o cluster crescer.

### Negativas / Trade-offs

- **Sem histórico**: `kubectl top` retorna apenas snapshot atual (~últimos 60s). Não é possível responder "qual foi o pico de memória ontem às 3h?" sem outro tooling.
- **Sem alertas automatizados**: incidentes (OOMKill, CrashLoopBackOff, NotReady) são detectados manualmente via runbook, não via PagerDuty/Slack/email automático.
- **Sem dashboards visuais**: dependência de CLI; equipes sem familiaridade com `kubectl` ficam cegas.
- **Sem métricas de aplicação**: endpoints `/metrics` (Prometheus exposition format) do backend e frontend ficam coletáveis no futuro, mas não são raspados hoje.
- **Sem logs centralizados**: stdout dos pods continua acessível apenas via `kubectl logs` (sem agregação, sem search, sem retenção controlada).
- **Não recomendado para produção crítica**: explicitamente aceito como fase MVP/workshop.

## Alternativas Consideradas

| Alternativa | Custo direto estimado/mês | Footprint cluster | Motivo da rejeição |
|---|---|---|---|
| `kube-prometheus-stack` completo agora | ~US$ 2–5 (EBS gp3 20 GiB + ALB compartilhado) | ~1.0–1.4 GiB RAM | **OOM risk crítico** — não cabe em `t3.micro x2`. Inviável até upgrade dos nodes. |
| CloudWatch Container Insights (mesmo enhanced) | US$ 5–15/mês para 2 nodes + ~10 pods | ~150–250 MiB (CloudWatch agent DaemonSet) | Fora do free tier (validado via aws-mcp). Vendor lock-in e custo recorrente sem necessidade no MVP. |
| Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG) | ~US$ 9 AMG/usuário + ~US$ 0.30 por 10M métricas AMP | ~50 MiB (apenas scraper) | AMG fora do free-tier (custo por usuário). Excessivo para 2 nodes. |
| Datadog / New Relic / Grafana Cloud | US$ 15–31/host/mês mínimo | ~100–200 MiB (agent) | Custo SaaS por host. Vendor lock-in. Foge do objetivo open-source/self-hosted do projeto. |
| Apenas `kubectl describe node` (sem metrics-server) | US$ 0 | 0 MiB | Não fornece valores numéricos de uso atual de pods, apenas requests/limits e estado. HPA fica inviável. |

## Roadmap de Evolução (pós free-tier)

A evolução prevista, em fases gatilhadas por sinais operacionais e/ou upgrade da infra:

### Fase 1 — Atual (free-tier, 2x t3.micro)
- `metrics-server` instalado em `kube-system`.
- Operação via `kubectl top` + runbook manual.
- Alertas: nenhum automatizado; checagem manual diária ou ad-hoc.
- **Gatilho para sair desta fase**: upgrade do node group para `t3.medium`+ OU mais de 3 incidentes operacionais por semana detectados tardiamente.

### Fase 2 — Observabilidade leve (2–3x t3.medium, sem PVC)
- Adicionar `kube-prometheus-stack` com:
  - Prometheus **sem persistência** (`persistence.enabled: false`) — retenção em memória, 6h.
  - Grafana **sem persistência** (dashboards versionados em Git via ConfigMap/Sidecar).
  - Alertmanager habilitado, mas com webhook único para Slack (sem PagerDuty).
  - kube-state-metrics + node-exporter habilitados.
  - **Sem Thanos, sem Loki, sem Tempo**.
- ServiceMonitors para backend (.NET via `prometheus-net.AspNetCore`) e frontend (Next.js via `prom-client`).
- Ingress via AWS Load Balancer Controller compartilhado com ArgoCD (TLS via ACM).
- Footprint esperado: ~0.7–1.0 GiB RAM no agregado.
- **Gatilho para sair desta fase**: necessidade de retenção > 1 dia OU primeiro post-mortem em que faltou histórico.

### Fase 3 — Observabilidade produção (3+x t3.large ou node group dedicado)
- Habilitar `persistence.enabled: true` para Prometheus e Grafana (PVC EBS gp3, 20 GiB cada).
- Retenção do Prometheus = 15 dias.
- Adicionar **Loki** para logs (single-binary mode, com S3 como backend → custo baixo, durabilidade alta).
- Adicionar **Promtail** ou Fluent Bit como DaemonSet para shipping de logs.
- Habilitar **Alertmanager HA** (2 réplicas) + integração PagerDuty se houver on-call formal.
- Configurar **recording rules** e **SLO tracking** (P95/P99 latência, error budget burn rate).
- Dashboards customizados das aplicações versionados em Git.
- **Gatilho para sair desta fase**: > 1 cluster (multi-cluster federation) OU retenção > 90 dias necessária.

### Fase 4 — Multi-cluster e long-term storage (opcional, futuro distante)
- **Thanos** ou **Mimir** com S3 como backend para long-term storage (retenção 1+ ano).
- **Grafana** consolidado (1 instância raspando múltiplos Prometheus via remote-read ou Thanos Query).
- Avaliação de **AMP/AMG** como alternativa managed se a equipe não quiser operar Thanos.
- Considerar OpenTelemetry para tracing distribuído (Tempo ou X-Ray).

## Critérios de Aceitação

- [ ] `metrics-server` instalado via Helm chart oficial no namespace `kube-system`
- [ ] `kubectl top nodes` retorna dados válidos (não vazio, não `<unknown>`)
- [ ] `kubectl top pods -A` retorna consumo de todos os pods em execução
- [ ] Resources `requests`/`limits` do `metrics-server` configurados (50m/64Mi → 100m/128Mi)
- [ ] PriorityClass `system-cluster-critical` aplicada ao deployment do `metrics-server`
- [ ] Todos os Deployments do projeto (`backend`, `frontend`, ArgoCD, ingress) com `resources.requests` e `resources.limits` definidos (pré-requisito para `kubectl top` ser interpretável)
- [ ] Runbook publicado em `docs/runbooks/observability-free-tier.md` com comandos e thresholds
- [ ] ADR-0007 anterior (`observabilidade-kube-prometheus-stack`) marcado como `Superseded by ADR-0007 (free-tier)` para evitar ambiguidade
- [ ] Documentado o gatilho explícito para promover à Fase 2 do roadmap (upgrade de nodes ou 3+ incidentes/semana)

## Referências

- AWS Well-Architected — Operational Excellence: monitoramento e métricas
- AWS EKS Userguide: [Kubernetes Metrics Server](https://docs.aws.amazon.com/eks/latest/userguide/metrics-server.html) (validado via aws-mcp)
- AWS CloudWatch Pricing: [Free Tier limits](https://aws.amazon.com/cloudwatch/pricing/) (validado via aws-mcp — Container Insights NÃO está no free tier)
- AWS EKS Best Practices: [Node and Workload Efficiency](https://docs.aws.amazon.com/eks/latest/best-practices/node_and_workload_efficiency.html)
- Kubernetes SIG: [metrics-server (upstream)](https://github.com/kubernetes-sigs/metrics-server)
- Provider Terraform: `hashicorp/helm` (validado via terraform-mcp; usar recurso nativo `helm_release`, não módulo comunitário)
- ADR-0003: EKS Cluster (base do node group atual)
- ADR-0006: ArgoCD GitOps (carga já residente no cluster)
- Regras do projeto: `.claude/rules/kubernetes-manifests.md` (resources obrigatórios em todos os Deployments)
