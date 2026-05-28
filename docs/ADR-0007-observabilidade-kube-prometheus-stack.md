# ADR-0007: Observabilidade com kube-prometheus-stack

**Status:** Proposed
**Data:** 2026-05-27
**Autores:** [Architect Agent]

## Contexto

A plataforma EKS `devops-ia-production` (us-east-1, v1.31, conforme ADR-0003) ja roda as aplicacoes backend (.NET 8) e frontend (Next.js) com deploy automatizado via GitHub Actions (ADR-0005) e ArgoCD (ADR-0006). O cluster nao possui hoje nenhuma solucao de metricas estruturada.

Para entrar em uso de producao real, a plataforma precisa de:

- Coleta de metricas de cluster (kube-state-metrics, node-exporter, cAdvisor via kubelet)
- Metricas de aplicacao expostas via endpoints `/metrics` (Prometheus exposition format)
- Visualizacao em dashboards
- Alertas operacionais para incidentes (pod crash loop, node not ready, alta latencia, OOMKilled)
- SLI/SLO tracking minimo: disponibilidade do backend e frontend, latencia P95/P99

### Estado atual do cluster relevante para a decisao

- **EKS**: `devops-ia-production`, v1.31, regiao `us-east-1`
- **Node group**: 2x `t3.medium` (2 vCPU, 4GB RAM cada) -- restricao de capacidade relevante
- **Storage classes disponiveis**: `gp2` (default); sera necessario habilitar EBS CSI driver para PVCs do Prometheus
- **AWS Account**: `654654554686`
- **Aplicacoes**: backend (.NET 8) e frontend (Next.js) -- ambas precisam expor `/metrics`
- **ArgoCD**: instalado conforme ADR-0006, sera a forma de deploy do stack de observabilidade
- **Volume de metricas estimado**: baixo (workshop/MVP) -- 10-50k series ativas no maximo

### Constraints

- Capacidade limitada do node group: o stack inteiro precisa caber em ~1.5 GB RAM disponivel apos overhead do cluster
- Workshop/MVP: alta disponibilidade do proprio stack de observabilidade nao e critica
- Sem dependencia externa paga inicialmente: nao usar Grafana Cloud, Datadog, New Relic
- Long-term storage opcional: pode ser adicionado em ADR futuro quando o volume justificar

## Decisao

Adotar o Helm chart **`kube-prometheus-stack`** (mantido pela `prometheus-community`) como solucao oficial de metricas, alertas e dashboards do cluster `devops-ia-production`.

### Componentes do stack

| Componente | Responsabilidade |
|---|---|
| Prometheus Operator | Gerencia CRDs (`ServiceMonitor`, `PodMonitor`, `PrometheusRule`, `AlertmanagerConfig`) |
| Prometheus | Coleta e armazenamento local de metricas |
| Alertmanager | Roteamento e supressao de alertas |
| Grafana | Visualizacao de dashboards |
| node-exporter | Metricas de host (CPU, memoria, disco, rede) |
| kube-state-metrics | Metricas do estado dos objetos Kubernetes |

### Namespace

Stack inteiro instalado no namespace dedicado **`monitoring`**. Nao usar `kube-system` para isolar workloads operacionais de workloads do cluster.

### Estrategia de retencao e armazenamento

- **Modo inicial (este ADR)**: Prometheus com **PVC EBS gp3** local
  - Retencao: **15 dias** (suficiente para troubleshooting e tendencias semanais)
  - Tamanho inicial do PVC: **20 GiB** (com room para crescer)
  - StorageClass: `gp3` (criar nova storage class; mais barata e performatica que `gp2`)
- **Remote write para Thanos/Mimir**: **rejeitado neste ADR**. O volume de metricas (workshop/MVP) nao justifica o custo operacional de Thanos sidecar + S3 + Compactor + Store. Sera revisitado em ADR futuro quando (a) retencao > 90 dias for requisito, (b) o cluster crescer alem de um node group, ou (c) houver multiplos clusters para federar.

### Coleta de metricas das aplicacoes

- Um **ServiceMonitor** por aplicacao, versionado e sincronizado pelo ArgoCD
- Backend (.NET 8): expor `/metrics` via `prometheus-net.AspNetCore` (porta 8080)
- Frontend (Next.js): expor `/metrics` via `prom-client` em um handler de API route (porta 3000)
- Selector dos ServiceMonitors: por label `app.kubernetes.io/name`

### Alertmanager: destino dos alertas

**Decisao: Slack**, via webhook em um canal dedicado (ex.: `#alerts-devops-ia`).

Justificativa:
- PagerDuty foi descartado porque o projeto ainda nao tem rotacao de on-call formal
- Slack ja e usado pela equipe e tem custo zero adicional
- Webhook URL armazenada em **AWS Secrets Manager** e injetada via External Secrets Operator (ESO) -- ou, na ausencia do ESO, via Secret Kubernetes criado fora do Git
- Severidades minimas: `critical` (page agora), `warning` (informativo), `info` (apenas log)

### Dashboards habilitados

Dashboards default do chart sao suficientes para uma primeira versao:
- `Kubernetes / Compute Resources / Cluster`
- `Kubernetes / Compute Resources / Namespace (Workloads)`
- `Kubernetes / Compute Resources / Pod`
- `Kubernetes / API server`
- `Kubernetes / Kubelet`
- `Node Exporter / Nodes`

Dashboards customizados para as aplicacoes (.NET e Next.js) serao adicionados em ADR de instrumentacao especifico.

### Acesso ao Grafana

**Decisao: Ingress via AWS Load Balancer Controller (ALB) com TLS terminado em ACM**.

LoadBalancer Service direto foi descartado porque cada Service `LoadBalancer` cria um ELB dedicado (~$16/mes cada); compartilhar ALB via Ingress reduz custo e simplifica DNS.

Pre-requisitos:
- AWS Load Balancer Controller instalado
- Certificado ACM em `us-east-1` para o dominio escolhido
- Registro DNS publico apontando para o ALB

Acesso interno (sem expor publicamente) sera possivel via `kubectl port-forward` enquanto o Ingress nao estiver pronto.

### IRSA (IAM Roles for Service Accounts)

Para este ADR, **IRSA nao e necessaria**: sem remote write para CloudWatch/Thanos, sem leitura direta de S3 pelo Prometheus. IRSA sera adicionada em ADR futuro se/quando Thanos sidecar (S3), CloudWatch remote write, ou CloudWatch Exporter forem incluidos.

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: Operator pattern automatiza CRDs e reconciliacao; chart amplamente testado; integracao nativa com ArgoCD via Application apontando para Helm chart.
2. **Security**: TLS no Ingress (ACM); Secrets em AWS Secrets Manager (com ESO); RBAC minimo via chart defaults; Grafana com login obrigatorio.
3. **Reliability**: Persistencia em EBS gp3 (durabilidade 99.999%); Alertmanager replicado nao e necessario no MVP (decisao consciente).
4. **Performance Efficiency**: Prometheus single-instance suficiente para o volume atual; gp3 com IOPS baseline (3000) supera gp2.
5. **Cost Optimization**: Sem remote storage pago; sem Grafana Cloud; um unico ALB compartilhado; retencao curta (15 dias) limita gasto de EBS.
6. **Sustainability**: Stack em um unico chart minimiza overhead; retencao curta reduz pegada de armazenamento.

## Consequencias

### Positivas

- Plataforma passa a ter visibilidade operacional minima de producao
- Stack open-source, sem dependencia de vendor
- Integracao nativa com Kubernetes via CRDs do Operator
- ArgoCD pode gerenciar o ciclo de vida do chart como qualquer outra Application
- Base pronta para evoluir para Thanos/Mimir, multi-cluster, ou SaaS quando o volume justificar

### Negativas / Trade-offs

- Stack consome ~1 vCPU e ~1.2 GB RAM no agregado -- aperta a capacidade do node group atual (`t3.medium` x2). Pode forcar expansao para `t3.large` ou um node group dedicado para observabilidade
- Retencao de 15 dias limita analise de tendencias mensais/trimestrais (mitigacao: ADR futuro de long-term storage)
- Sem HA do Prometheus/Alertmanager: perda de visibilidade durante downtime do pod do Prometheus (aceito no MVP)
- Acoplamento ao Helm chart `kube-prometheus-stack`: upgrades majors historicamente exigem migracao de CRDs (mitigado lendo release notes a cada upgrade)

## Alternativas Consideradas

| Alternativa | Motivo da rejeicao |
|---|---|
| Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG) | Custo recorrente significativo para volume MVP (>$50/mes apenas pelo AMG workspace); reduz aprendizado de Prometheus self-hosted -- valor pedagogico do workshop |
| CloudWatch Container Insights | Vendor lock-in; menor flexibilidade em PromQL; custo por GB ingerido cresce rapido |
| Prometheus + Grafana instalados separadamente via manifests proprios | Reinventa a roda; perde Operator e CRDs; muito mais codigo para manter |
| Datadog / New Relic / Grafana Cloud | Custo SaaS por host/serie; foge do objetivo de plataforma self-hosted |
| Victoria Metrics | Excelente performance, mas curva de aprendizado maior; ecossistema de alerting menos rico; comunidade menor |

## Criterios de Aceitacao

- [ ] Namespace `monitoring` criado com labels padrao
- [ ] EBS CSI driver instalado como EKS addon
- [ ] StorageClass `gp3` criada (default ou explicita no PVC do Prometheus)
- [ ] Helm chart `kube-prometheus-stack` instalado via ArgoCD Application
- [ ] PVC do Prometheus criado com 20 GiB em `gp3`
- [ ] Retencao do Prometheus configurada em 15 dias
- [ ] node-exporter rodando como DaemonSet nos 2 nodes
- [ ] kube-state-metrics rodando como Deployment
- [ ] ServiceMonitors para backend e frontend criados
- [ ] Alertmanager configurado com receiver Slack
- [ ] Webhook do Slack armazenado em Secret Kubernetes (futura migracao para AWS Secrets Manager via ESO)
- [ ] Ingress ALB criado para acesso ao Grafana com TLS via ACM
- [ ] Dashboards default do chart visiveis e populados com dados
- [ ] Alertas default do chart (`KubePodCrashLooping`, `KubeNodeNotReady`, etc.) ativos
- [ ] ArgoCD Application em estado `Synced` e `Healthy`
- [ ] Documentacao de acesso ao Grafana publicada em `docs/runbooks/` ou equivalente
