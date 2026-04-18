# Orquestrador AIOps — Integrações

## Integração com WebAI (Open WebUI)

O Open WebUI suporta **Funções** (anteriormente Pipelines) que podem encaminhar requisições para APIs externas.

### Opção A: Função do Open WebUI (recomendada)

O Open WebUI v0.8+ suporta funções Python customizadas que podem interceptar mensagens e chamar APIs externas.

1. No painel de administração do Open WebUI, vá em **Functions**
2. Crie uma nova função com este código:

```python
"""
title: AIOps Orchestrator Bridge
description: Forwards task requests to the AIOps Orchestrator
version: 0.1.0
"""

import json
import httpx
from typing import Optional

ORCHESTRATOR_URL = "http://aiops-orchestrator:8000"  # Docker network name
API_TOKEN = "YOUR_TOKEN_HERE"  # From /opt/aiops/.env

class Filter:
    def __init__(self):
        self.prefix = "/task"  # Messages starting with /task get forwarded

    async def inlet(self, body: dict, __user__: dict) -> dict:
        messages = body.get("messages", [])
        if not messages:
            return body

        last_message = messages[-1].get("content", "")

        # Only intercept messages starting with /task
        if not last_message.startswith(self.prefix):
            return body

        task_message = last_message[len(self.prefix):].strip()
        if not task_message:
            messages[-1]["content"] = "Usage: /task <your request>"
            return body

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{ORCHESTRATOR_URL}/v1/chat/ingest",
                    headers={
                        "Authorization": f"Bearer {API_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "message": task_message,
                        "user_id": __user__.get("id", "webai-user"),
                    },
                )
                result = resp.json()

                # Format response for chat
                status = result.get("status", "unknown")
                task_id = result.get("task_id", "N/A")
                summary = result.get("summary", "")
                message = result.get("message", "")
                risk = result.get("risk_level", "")

                response = f"**AIOps Task {task_id[:8]}**\n"
                response += f"- Status: `{status}`\n"
                if risk:
                    response += f"- Risk: `{risk}`\n"
                if summary:
                    response += f"- Summary: {summary}\n"
                if message:
                    response += f"\n{message}"
                if status == "awaiting_approval":
                    response += f"\n\n⚠️ Requires approval. Use `/approve {task_id[:8]}` to approve."

                messages[-1]["content"] = response
        except Exception as e:
            messages[-1]["content"] = f"Error connecting to AIOps: {str(e)}"

        return body
```

3. Enable the function and assign it to your model

### Opção B: Chamada direta à API from Chat

Users can interact with the orchestrator directly via curl from the WebAI terminal or any HTTP client:

```bash
curl -X POST http://192.168.3.155:8000/v1/chat/ingest \
    -H "Authorization: Bearer YOUR_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message": "check disk usage on docker CT"}'
```

## Configuração do Ollama

Ollama should already be running on your PC (192.168.3.87:11434).

### Verify Connectivity
```bash
curl http://192.168.3.87:11434/api/tags
```

### Configure in AIOps
In `/opt/aiops/.env`:
```
AIOPS_OLLAMA_BASE_URL=http://192.168.3.87:11434
AIOPS_OLLAMA_DEFAULT_MODEL=llama3.1:8b
```

### Recommended Models
- **Classification**: `llama3.1:8b` (fast, good for intent parsing)
- **Planning**: `llama3.1:70b` or `deepseek-coder:33b` (if you have VRAM)
- **Summarization**: `llama3.1:8b` or `phi3:mini`

## Configuração da API Claude

### Get API Key
1. Go to https://console.anthropic.com/
2. Create an API key
3. Add to `/opt/aiops/.env`:
```
AIOPS_CLAUDE_API_KEY=<anthropic-api-key>
AIOPS_CLAUDE_MODEL=claude-sonnet-4-20250514
```

### Cost Control
Claude is used for **planning and review only**, not classification or summarization. This keeps costs low while leveraging Claude's superior reasoning for critical decisions.

## OpenAI-Compatible API

Works with OpenAI, Azure OpenAI, local endpoints (vLLM, LocalAI, LM Studio), or any compatible API.

### Configure
```
AIOPS_OPENAI_API_KEY=sk-...
AIOPS_OPENAI_BASE_URL=https://api.openai.com/v1
AIOPS_OPENAI_MODEL=gpt-4o
```

### Local Endpoint Example (LM Studio)
```
AIOPS_OPENAI_API_KEY=not-needed
AIOPS_OPENAI_BASE_URL=http://192.168.3.87:1234/v1
AIOPS_OPENAI_MODEL=local-model
```

## Prometheus / Grafana Integration

### Add Scrape Target
In CT 200, edit `/opt/monitoring/prometheus/prometheus.yml` and add:

```yaml
  - job_name: aiops_orchestrator
    scrape_interval: 30s
    static_configs:
      - targets:
          - 192.168.3.155:8000
    metrics_path: /metrics
```

Then reload Prometheus:
```bash
pct exec 200 -- docker exec prometheus kill -HUP 1
# or
curl -X POST http://192.168.3.200:9090/-/reload
```

### Painel Grafana
A basic Grafana dashboard can query these metrics:
- `aiops_tasks_total` - Task volume
- `aiops_tasks_by_status` - Task lifecycle funnel
- `aiops_approvals_pending` - Pending human reviews
- `aiops_blocked_actions_total` - Security blocks
- `aiops_provider_calls_total` - LLM usage

## Code Executor Integration (Future)

The orchestrator is designed to integrate with code execution agents:

### Interface
Any code executor that accepts HTTP commands can be integrated by:
1. Creating a new adapter in `app/adapters/`
2. Implementing `BaseExecutorAdapter` interface
3. Adding to provider registry
4. Configuring in `config/providers.yml`

### Codex/Copilot CLI Bridge (Prepared)
```python
# Example future adapter: app/adapters/codex_bridge.py
class CodexBridgeAdapter(BaseExecutorAdapter):
    name = "codex"
    async def execute(self, command, **kwargs):
        # Forward to codex CLI or API
        ...
```

The executor adapter interface provides: command, cwd, timeout, dry_run, env.
All safety checks (policy, approval, audit) still apply.
