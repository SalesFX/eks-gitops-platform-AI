# Runbook: Triagem Semanal de Findings de Segurança

**ADR de referência:** ADR-0009 (Segurança da Pipeline)
**Frequência:** Semanal (toda segunda-feira, duração estimada: 20–30 min)
**Última revisão:** 2026-05-27

---

## Onde encontrar os findings

1. **GitHub Security tab** → Code scanning alerts:
   `https://github.com/SalesFX/aws-devops-platform/security/code-scanning`
2. **GitHub Security tab** → Secret scanning:
   `https://github.com/SalesFX/aws-devops-platform/security/secret-scanning`
3. **GitHub Actions** → Workflow `security-scheduled.yml` → Job Summary de cada execução diária.

---

## Política de severidade (ADR-0009)

| Severidade | SLA | Ação |
|------------|-----|------|
| CRITICAL | Imediato (bloqueia merge) | Corrigir antes de qualquer merge em `main` |
| HIGH | 7 dias corridos | Abrir issue, assignar responsável, resolver dentro da janela |
| MEDIUM | 30 dias | Registrar na triagem, priorizar conforme backlog |
| LOW / INFO | 90 dias ou ignorar | Opcional — documentar se for falso positivo |

---

## Fluxo de triagem semanal

### Passo 1: Revisar novos findings CRITICAL (< 5 min)

```
GitHub Security tab → Code scanning alerts
Filtrar: State = Open, Severity = Critical
```

Se houver CRITICAL aberto: **é urgente — não aguardar a triagem semanal.**
Bloquear merges até resolução ou adição ao `.trivyignore` com justificativa e expiry.

### Passo 2: Revisar findings HIGH com SLA vencendo (< 10 min)

```
Filtrar: State = Open, Severity = High
Ordenar por: Created (mais antigo primeiro)
```

Para cada HIGH:
1. Verificar se o CVE/finding tem fix disponível upstream.
2. Se sim: criar/atualizar PR com a dependência corrigida.
3. Se não: adicionar ao arquivo de allowlist correspondente com expiry de 30 dias:
   - CVE de container/fs: `.trivyignore`
   - Finding de IaC: `.checkov.yml` (skip-check com justificativa)
   - Secret falso positivo: `.gitleaks.toml` (allowlist)
   - SAST falso positivo: `.semgrepignore` ou `# nosemgrep: <rule-id>` inline

### Passo 3: Validar findings do scan scheduled overnight (< 10 min)

Revisar o output do último `security-scheduled.yml` (run mais recente):
```
GitHub Actions → Workflows → Security Scans — Scheduled → última execução
```

Findings novos (que não existiam no último commit) = CVEs publicados após o último build.
Estes têm prioridade: podem afetar imagens já em produção.

### Passo 4: Atualizar registro de findings

Adicionar entrada no log informal (pode ser uma issue no GitHub ou doc interno):

```
Data: YYYY-MM-DD
Novos findings: X CRITICAL, Y HIGH, Z MEDIUM
Resolvidos nesta semana: X
Em progresso (com issue aberta): Y
Adicionados a allowlist (com expiry): Z
```

---

## Adicionando finding ao allowlist

### .trivyignore (CVE de container ou filesystem)

```
# Adicionar linha no arquivo .trivyignore:
CVE-2024-XXXXX  # expires: YYYY-MM-DD  <justificativa: sem fix, mitigado por X>
```

**Regras:**
- `expires` é obrigatório. Máximo 90 dias para CRITICAL sem fix, 180 dias para HIGH.
- Justificativa é obrigatória.
- Revisar na triagem da semana após expiração.

### .checkov.yml (misconfiguration de IaC)

```yaml
# Adicionar no skip-check do .checkov.yml:
skip-check:
  - CKV_AWS_XXXX  # ADR-YYYY: <justificativa e referência ao trade-off documentado>
```

### .gitleaks.toml (falso positivo de secret)

```toml
[[allowlist.regexes]]
description = "<descrição do falso positivo>"
regex = '''<padrão do falso positivo>'''
```

### .semgrepignore (falso positivo de SAST)

Adicionar path ou padrão ao `.semgrepignore`.
Para ignorar em linha no código: `# nosemgrep: <rule-id>` com comentário explicativo.

---

## Escalação

Se um CRITICAL não tiver fix disponível upstream e o expiry precisar ultrapassar 90 dias:
1. Documentar o CVE, o contexto e a mitigação alternativa em `docs/security-exceptions.md`.
2. Obter revisão e aprovação de segundo par.
3. Adicionar ao `.trivyignore` com expiry de 90 dias + link para o documento.

---

## Referências

- ADR-0009: Segurança da Pipeline e Scans Automatizados
- GitHub Code Scanning: https://docs.github.com/en/code-security/code-scanning
- Trivy ignore: https://aquasecurity.github.io/trivy/latest/docs/configuration/filtering/
- Checkov skip: https://www.checkov.io/2.Basics/Suppressing%20and%20Skipping%20Policies.html
