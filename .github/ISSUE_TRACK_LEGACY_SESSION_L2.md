Title: Track legacy endpoint usage before Session L2 removal planning

Session L1 foi concluída no commit `807faa3`.

Runtime validado:
- `/health`: OK
- `/ready`: OK
- legacy headers OK em `/v1/providers/status` e `/v1/tasks`
- métrica `aiops_legacy_endpoint_hits_total` confirmada

Valores iniciais:
- `providers_status`: 2
- `tasks_collection`: 2

Critério antes da L2:
- observar uso real por alguns dias/semanas
- identificar consumidores de endpoints legados
- não remover rotas até confirmar ausência de tráfego ou plano de migração
