# AIOps Orchestrator — Segurança

## Modelo de segurança

### Confiança zero entre chat e executor

- O chat (WebUI) autentica no orquestrador via token Bearer
- Toda requisição é autenticada antes do processamento
- Sem acesso direto via shell do chat a qualquer sistema
- Todos os comandos passam pelo motor de políticas antes da execução
- No v1 (Diagnostic Engine), nenhum executor real é chamado

### Defesa em profundidade

```text
Layer 1: Authentication (Bearer token)
Layer 2: Intent Classification (LLM risk assessment)
Layer 3: Policy Engine (hardcoded denylist + regras configuráveis)
Layer 4: Action Catalog (allowlist estrutural — validado no boot)
Layer 5: Plan Validation (cada action_id verificado contra catálogo)
Layer 6: Approval Gate (revisão humana obrigatória)
Layer 7: Execution Safety (timeout, masking, backup)
Layer 8: Audit Trail (log completo de cada decisão e ação)
```

### Validação do catálogo no boot

O catálogo `config/actions.yaml` é **validado durante o startup** da aplicação via
`init_catalog_on_startup()` (lifespan em `app/main.py`). A falha de validação é detectada antes
da primeira requisição, não na primeira chamada de endpoint:

| Estado do catálogo | Efeito no startup              | Efeito nos endpoints                       |
| ------------------ | ------------------------------ | ------------------------------------------ |
| Válido             | Cache populado, log INFO       | Endpoints de catálogo/plano funcionam      |
| Inválido/ausente   | Cache vazio, log ERROR         | `/ready` → `not_ready`; catalog/plan → 503 |
| Inválido/ausente   | Readiness degradada            | `/diagnose` → 200 com `action_plan: null`  |

Nenhum `command` ou conteúdo do catálogo é exposto no `/ready` ou em qualquer resposta de erro.

### Hardcoded Denylist (não pode ser sobrescrita)

Estas operações são **sempre bloqueadas**, independente do modo de política:

- `rm -rf /` e variantes
- `mkfs`, `fdisk`, `parted` (formatação de disco)
- `dd if=... of=/dev/...` (escrita raw em disco)
- `shutdown`, `reboot`, `halt`, `poweroff`
- `systemctl disable/mask` em serviços críticos
- `pct destroy`, `qm destroy`
- `docker system prune`
- `docker rm -f` em containers protegidos
- `iptables -F` (flush de firewall)
- `ip route del/flush`
- `chmod -R 777 /`, `chown -R ... /`
- Escrita em `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`
- `curl | bash`, `wget | sh` (execução remota de código)

### `/v1/aiops/diagnose` — garantias de não-execução

O endpoint de diagnóstico **nunca executa** nada. Todos os campos de saída são informativos:

- `recommended_actions`: texto descritivo com `command: null` — nenhum comando executável
- `action_plan`: plano estruturado com `action_ids` do catálogo allowlisted, sempre `dry_run: true`
- Nenhum `command` aparece em qualquer campo da resposta
- Falha no catálogo de actions retorna `action_plan: null` — diagnose segue com 200 (fail-soft)
- Nenhum executor local, SSH ou Docker é chamado durante o diagnóstico

### Action Mapper — garantias

O mapeador (`app/agent_router/services/action_mapper.py`) converte findings em `action_ids`:

- Tabela estática (`_CHECK_ACTION_MAP`) — sem LLM, sem texto livre, sem interpolação
- Saída são apenas strings `action_id` — nenhum campo `command` ou shell string
- Nomes de sinais / checks desconhecidos são ignorados silenciosamente
- Deduplicação: mesmo `action_id` aparece no máximo uma vez na saída

### Action Planner — garantias de segurança

O Action Planner (`app/services/action_planner.py`) seleciona ações **somente** do catálogo
allowlisted. É determinístico, sem LLM e sem comando livre:

- Aceita apenas `action_ids` explícitos — nenhuma string livre é interpretada como comando
- Cada `action_id` é verificado contra o índice em memória carregado de `config/actions.yaml`
- `action_ids` desconhecidos vão para `blocked_steps` (fail-closed)
- Aplica policy gate independente: `mode != readonly` ou `risk != low` → `blocked_steps`
- Nenhum `command` aparece na resposta do plano
- `dry_run: true` é invariante na resposta
- Catálogo ausente ou inválido retorna HTTP 503 (fail-closed nos endpoints standalone)

### Catálogo de actions (allowlist estrutural)

O catálogo `config/actions.yaml` define ações explicitamente permitidas com metadados obrigatórios:

| Campo               | Obrigatório | Descrição                                    |
| ------------------- | ----------- | -------------------------------------------- |
| `action_id`         | sim         | Identificador único da ação                  |
| `mode`              | sim         | `readonly` — único modo permitido no v1      |
| `risk`              | sim         | `low`, `medium`, `high`                      |
| `timeout_seconds`   | sim         | Limite de tempo de execução                  |
| `requires_approval` | sim         | `true`/`false` — aprovação humana            |

**No v1, apenas ações `mode: readonly` e `risk: low` constam no catálogo.**

Comandos bloqueados no catálogo (validados por `scripts/validate_actions_catalog.sh`):

- `rm` (qualquer variante)
- `chmod 777`
- `docker exec`
- `ssh`
- `curl | bash` e qualquer pipe para shell
- `git push`
- `docker compose up`
- `systemctl restart` / `start` / `stop` / `disable`

### Modos de política

| Modo          | Risco baixo   | Risco médio | Risco alto  | Bloqueado |
| ------------- | ------------- | ----------- | ----------- | --------- |
| `safe`        | Auto-executar | Aprovar     | Aprovar     | Negar     |
| `supervised`  | Aprovar       | Aprovar     | Aprovar     | Negar     |
| `manual-only` | Aprovar       | Bloquear    | Bloquear    | Negar     |

**Padrão: `supervised`** — toda ação requer aprovação humana explícita.

### Recursos protegidos

- **Serviços:** prometheus, grafana, npm, open-webui, nextcloud, adguard, docker, sshd
- **Containers:** CT 102 (docker), CT 103 (adguard), CT 200 (monitor)
- **VMs:** VM 100 (omv-nas), VM 101 (win-bi-plex)

### Gerenciamento de segredos

- API keys armazenadas somente em `.env` (não em YAML de configuração)
- `.env` excluído do controle de versão (`.gitignore`)
- Segredos são mascarados em logs e saída de comandos
- Bearer tokens, API keys e senhas são auto-detectados e mascarados

### Segurança de rede

- Orchestrator escuta em `0.0.0.0:8000` (rede interna apenas)
- CORS restrito à rede local e domínio conhecido
- Reverse proxy (NPM) faz terminação SSL externa
- Sem exposição pública por padrão
- `/metrics` deve ser restrito ao IP do Prometheus se exposto externamente

---

## Checklist de segurança

- [ ] Alterar token padrão da API em `.env`
- [ ] Verificar que o modo de política está em `supervised` ou `manual-only`
- [ ] Revisar `config/policies.yml` para o ambiente
- [ ] Revisar `config/actions.yaml` e remover qualquer ação não necessária
- [ ] Executar `scripts/validate_actions_catalog.sh` antes de qualquer deploy
- [ ] Se exposto externamente, usar NPM com SSL
- [ ] Restringir `/metrics` ao IP do Prometheus se necessário
- [ ] Monitorar `aiops_blocked_actions_total` para detecção de abuso
- [ ] Backup regular de `aiops.db` para preservação da trilha de auditoria

---

## O que NÃO está implementado no v1 (por design)

| Capacidade              | Status no v1 | Motivo                                     |
| ----------------------- | ------------ | ------------------------------------------ |
| Shell livre via LLM     | Bloqueado    | Risco de execução arbitrária               |
| SSH remoto              | Isolado      | Superfície remota sem allowlist estrutural |
| Docker exec             | Isolado      | Alteração operacional real via shell       |
| Remediação automática   | Bloqueado    | Requer approval gate explícito             |
| `git push` / deploy     | Bloqueado    | Alteração de estado produtivo              |
| `systemctl restart`     | Bloqueado    | Reinício de serviço sem aprovação          |
| `docker compose up`     | Bloqueado    | Alteração de stack sem aprovação           |
