# Registro canônico dos repositórios

**Observado em:** 30 de julho de 2026, 12:32:30 (America/Sao_Paulo)  
**Fonte:** consulta viva pelo conector GitHub.  
**Regra:** branches e HEADs abaixo são checkpoint; revalidar antes de qualquer ação.

| Repositório | Branch padrão | Visibilidade | Papel | HEAD observado |
|---|---|---|---|---|
| [`mglpsw/homelab`](https://github.com/mglpsw/homelab) | `main` | private | `canonical_homelab_infrastructure` | `06abd85a893c` |
| [`mglpsw/AgentEscala`](https://github.com/mglpsw/AgentEscala) | `develop` | private | `medical_workforce_product` | `2936538a84cd` |
| [`mglpsw/agent-sandbox`](https://github.com/mglpsw/agent-sandbox) | `main` | public | `public_agent_experimentation_sandbox` | `75f7a699d7be` |
| [`mglpsw/agent-router-api`](https://github.com/mglpsw/agent-router-api) | `master` | private | `llm_inference_gateway_and_adaptive_router` | `96cb3e9994bb` |
| [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator) | `master` | public | `aiops_runtime_and_agentreview_engine` | `620242eec8c6` |
| [`mglpsw/interleitos`](https://github.com/mglpsw/interleitos) | `main` | private | `clinical_transfer_and_bed_regulation_product` | `43e567b2fd61` |

## Relações canônicas

```text
AgentEscala ─┐
             ├─ target repos ─→ aiops-orchestrator / AgentReview ─→ agent-router-api
InterLeitos ─┘                                      │                     │
                                                    └──── contracts ──────┘

homelab = infraestrutura, ambientes, monitoramento e runbooks
agent-sandbox = experimentação pública e descartável
```

- `AgentReview` é subsistema do `mglpsw/aiops-orchestrator`, não repositório separado.
- `agent-router-api` é gateway e executor de política de inferência; não decide readiness, cobertura semântica do produto nem publicação.
- `homelab` é a fonte canônica da infraestrutura; produto e Router não devem reabsorver Proxmox/CT/Prometheus/Grafana.
- InterLeitos possui `canonical_dev` no CT104, mas **não possui produção canônica provisionada ou autorizada** neste corte.
