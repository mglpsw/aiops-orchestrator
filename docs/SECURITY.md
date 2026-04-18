# Orquestrador AIOps — Segurança

## Modelo de segurança

### Confiança zero entre chat e executor
- O chat (WebAI) autentica no orquestrador via token Bearer
- Toda requisição é autenticada antes do processamento
- Sem acesso direto via shell do chat a qualquer sistema
- Todos os comandos passam pelo motor de políticas antes da execução

### Defesa em profundidade

```
Layer 1: Authentication (Bearer token)
Layer 2: Intent Classification (LLM risk assessment)
Layer 3: Policy Engine (hardcoded denylist + rules)
Layer 4: Plan Validation (each step checked)
Layer 5: Approval Gate (human review for risky ops)
Layer 6: Execution Safety (timeout, masking, backup)
Layer 7: Audit Trail (full logging)
```

### Hardcoded Denylist (Cannot Be Overridden)

These operations are **always blocked**, regardless of policy mode:

- `rm -rf /` and variants
- `mkfs`, `fdisk`, `parted` (disk formatting)
- `dd if=... of=/dev/...` (raw disk write)
- `shutdown`, `reboot`, `halt`, `poweroff`
- `systemctl disable/mask` on critical services
- `pct destroy`, `qm destroy`
- `docker system prune`
- `docker rm -f` on protected containers
- `iptables -F` (firewall flush)
- `ip route del/flush`
- `chmod -R 777 /`, `chown -R ... /`
- Writing to `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`
- `curl|bash`, `wget|sh` (remote code execution)

### Policy Modes

| Mode | Low Risk | Medium Risk | High Risk | Blocked |
|------|----------|-------------|-----------|---------|
| `safe` | Auto-execute | Approve | Approve | Deny |
| `supervised` | Approve | Approve | Approve | Deny |
| `manual-only` | Approve | Block | Block | Deny |

### Protected Resources

- **Services**: prometheus, grafana, npm, open-webui, nextcloud, adguard, docker, sshd
- **Containers**: CT 102 (docker), CT 103 (adguard), CT 200 (monitor)
- **VMs**: VM 100 (omv-nas), VM 101 (win-bi-plex)

### Gerenciamento de segredos

- API keys stored only in `.env` file (not in config YAML)
- `.env` is excluded from version control
- Secrets are masked in logs and command output
- Bearer tokens, API keys, and passwords auto-detected and masked

### Network Security

- Orchestrator listens on 0.0.0.0:8000 (internal network only)
- CORS restricted to local network and known domain
- Reverse proxy (NPM) handles external SSL termination
- No public exposure by default

## Security Checklist

- [ ] Change default API token in `.env`
- [ ] Verify policy mode is `supervised` or `manual-only`
- [ ] Review `config/policies.yml` for your environment
- [ ] If exposing externally, use NPM with SSL
- [ ] Restrict `/metrics` endpoint to Prometheus IP if needed
- [ ] Monitor `aiops_blocked_actions_total` metric for abuse
- [ ] Regular backup of `aiops.db` for audit trail preservation
