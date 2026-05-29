# Writing Style

## Names and Titles

Write names naturally, like a human wrote them — not like a document generator formatted them.

**No em-dashes (travessão "—") anywhere:**

```
# wrong
IaC Scan — Terraform (Checkov)
SAST — Frontend (Semgrep)
Security Scans — Scheduled

# right
Terraform IaC Scan (Checkov)
Frontend SAST (Semgrep)
Scheduled Security Scans
```

This applies to: GitHub Actions job names, step names, workflow names, PR titles, commit messages, ADR titles, README section headings, and any other user-visible text. Em-dashes in generated names are a strong AI tell.

**Alternatives to the em-dash pattern:**
- Flip subject and qualifier: `SCA — Backend` → `Backend SCA`
- Use "on" for location: `scan — frontend` → `scan on frontend`
- Use plain comma for lists/asides: `found HIGH vulnerabilities — review required` → `found HIGH vulnerabilities, review required`
- Use parentheses for clarification: `Backend SCA (.NET audit + Trivy)`

## Tone

Write in natural prose. Avoid overly formal or structured phrasing that sounds templated. Bullet points are fine; pseudo-formal section separators like `━━━━` in user-facing output are acceptable when they help readability.
