# Release Notes

## Checkpoint da fase AIOps

### Sessions 13–18

Esta sequência consolidou a base canônica do AIOps Orchestrator em modo seguro,
read-only e auditável.

- runner read-only allowlisted
- approval gate persistente
- histórico de runs e auditoria
- redaction forte de segredos, tokens e headers
- Prometheus allowlisted sem PromQL livre
- diagnóstico inteligente com findings estruturados
- GitHub Agent Review on-demand
- chat/OpenWebUI com intents AIOps determinísticas

### Session 18

- integração do chat/OpenWebUI ao fluxo operacional existente
- roteamento determinístico para diagnose, runs, approvals e status
- respostas curtas em pt-BR
- fallback seguro para o fluxo normal quando a mensagem não é AIOps
- documentação atualizada com o checkpoint final da fase

### Garantias preservadas

- sem shell livre
- sem SSH
- sem `docker exec`
- sem deploy automático
- sem actions novas
- sem runner novo
- sem execução de actions pelo chat
- sem exposição de secrets, headers ou payload bruto

### Próxima fase

O próximo foco recomendado é `agent-router-api`, com fronteiras explícitas entre chat,
diagnóstico e qualquer superfície futura de execução.
