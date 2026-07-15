# Manual Oficial do Projeto AIOps / Homelab

## Estado atual, arquitetura, operação, auditoria e roadmap

**Documento:** AIOPS-MANUAL-001  
**Versão do documento:** 1.0  
**Data de consolidação:** 15 de julho de 2026  
**Baseline do código:** após o merge da PR `mglpsw/aiops-orchestrator#66`  
**Release produtivo do runtime:** `v0.19.0`  
**Release track em desenvolvimento:** `v0.20.0 — AgentReview Quality Gate`  
**Status do documento:** baseline oficial sugerida; ainda não versionada no repositório

---

## 1. Finalidade deste manual

Este documento consolida o estado técnico e operacional do projeto AIOps / Homelab, com foco no repositório [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator), na integração com [`mglpsw/AgentEscala`](https://github.com/mglpsw/AgentEscala) e no papel do `agent-router-api`.

Ele deve servir como:

- manual de arquitetura;
- baseline de operação;
- inventário do que funciona hoje;
- registro das fronteiras de segurança;
- diagnóstico das lacunas ainda abertas;
- guia de implementação do release track `v0.20.0`;
- roteiro para ativar rapidamente o quality gate no AgentEscala.

### 1.1 Hierarquia das fontes de verdade

Em caso de divergência, usar esta ordem:

1. Código mergeado em `master`.
2. Schemas e testes versionados no repositório.
3. Release e evidências de aceite em issues/PRs.
4. Documentação versionada em `docs/`.
5. Este manual.
6. Decisões de planejamento ainda não mergeadas.

Este manual diferencia explicitamente:

- **implementado e validado**;
- **implementado, mas ainda não integrado ponta a ponta**;
- **planejado**;
- **fora de escopo permanente**.

---

## 2. Resumo executivo

O projeto possui hoje duas superfícies independentes, que não devem ser confundidas:

```text
A. Runtime operacional AIOps no CT102
B. AgentReview Tool Repo no CT104
```

O runtime CT102 concluiu a transição para `v0.19.0`, com health, readiness, metrics, database, provider registry, action catalog e stores operacionais validados. O AgentReview não roda nesse ambiente.

No CT104, o `aiops-orchestrator` já funciona como motor offline e determinístico para:

```text
intake/redaction
→ semantic chunk planning
→ structured chunk parsing
→ final review synthesis
→ deterministic quality gate
```

A PR #66, já mergeada, adicionou o quality gate determinístico e o artifact:

```text
review-quality-gate.json
```

O projeto está, portanto, em uma transição de maturidade:

```text
v0.19.0
= engine E2E até final-review + runtime CT102 estabilizado

v0.20.0
= gate determinístico integrado ao contrato E2E e consumido pelo target repo
```

### 2.1 Situação em uma frase

> O AIOps já consegue produzir e validar deterministicamente um review final, mas ainda falta tornar o `review-quality-gate.json` parte do E2E oficial e fazer o AgentEscala consumi-lo como thin-wrapper.

### 2.2 Próximo bloqueio real

A próxima PR deve ser a issue #60:

```text
#60 — test(agent-review): add quality gate E2E contract fixture
```

Depois dela, o caminho mais curto para gerar valor no AgentEscala é:

```text
#60 E2E do gate
→ #65 contrato do wrapper
→ PR própria no AgentEscala para consumir review-quality-gate.json
→ canário no CT104
```

---

## 3. Escopo da auditoria

Foram considerados:

- histórico consolidado do release `v0.19.0`;
- roadmap de migração AgentEscala ↔ AIOps;
- issues fechadas #29, #37, #38, #39, #40, #41 e #52;
- issues abertas #42, #43, #44, #45 e #46;
- release track #58;
- issues executáveis #59 a #65;
- PR #66 e sua validação final;
- código e schemas do AgentReview;
- documentação de engine, E2E e release;
- fronteiras CT104/CT102;
- pipeline de artifacts;
- riscos de segurança e falsos positivos.

### 3.1 Limite da auditoria

Esta é uma auditoria de engenharia e arquitetura baseada no repositório, evidências de CI e contexto operacional consolidado. Não substitui:

- pentest;
- auditoria de segredos em todos os hosts;
- inspeção forense do CT102;
- validação clínica das regras de escala médica;
- revisão manual de cada linha histórica do projeto.

Nenhum blocker crítico novo foi identificado no escopo revisado, mas isso não equivale a uma certificação formal de segurança.

---

## 4. Arquitetura oficial

## 4.1 Visão de alto nível

```text
┌─────────────────────────────────────────────────────────────────┐
│ AgentEscala                                                     │
│ - produto e target repo                                         │
│ - contratos médicos/operacionais                                │
│ - geração de artifacts                                          │
│ - workflow thin-wrapper                                         │
│ - publicação do comentário no PR                                │
└───────────────────────┬─────────────────────────────────────────┘
                        │ artifacts locais no CT104
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ aiops-orchestrator — AgentReview Tool Repo no CT104              │
│ - loaders/intake                                                 │
│ - redaction                                                      │
│ - semantic chunk planner                                         │
│ - structured parser                                              │
│ - final synthesizer                                              │
│ - deterministic quality gate                                     │
│ - telemetry e learning controlado, futuramente                   │
└───────────────────────┬─────────────────────────────────────────┘
                        │ chamada de modelo somente quando habilitada
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ agent-router-api                                                 │
│ - model gateway OpenAI-compatible                                │
│ - endpoint canônico: /v1/chat/completions                        │
│ - não executa shell/deploy/remediação                             │
└─────────────────────────────────────────────────────────────────┘

Separado operacionalmente:

┌─────────────────────────────────────────────────────────────────┐
│ aiops-orchestrator Runtime no CT102                              │
│ - produção                                                       │
│ - health/readiness/metrics                                       │
│ - diagnóstico, planos, aprovações e ações read-only allowlisted  │
│ - audit/run history e stores                                     │
│ - não roda AgentReview tooling                                   │
└─────────────────────────────────────────────────────────────────┘
```

## 4.2 Matriz de responsabilidade

| Componente | Local | Responsabilidade principal | Não deve fazer |
|---|---|---|---|
| AgentEscala | Target repo / CT104 workflow | Produzir diff, contexto, contratos e artifacts; consumir outputs; publicar comentário | Reimplementar engine genérico; chamar CT102; executar comandos de LLM |
| AIOps AgentReview | CT104 toolrepo | Sanitizar, planejar chunks, normalizar findings, sintetizar e aplicar quality gate | Deploy, restart, SSH, Docker exec, GitHub write, provider direto |
| Agent Router | Serviço de roteamento | Encaminhar requests de modelo via `/v1/chat/completions` | Ser review engine; executar ações operacionais |
| AIOps Runtime | CT102 | Runtime produtivo, diagnóstico, auditoria e execução read-only allowlisted | Ser staging de PR; rodar AgentReview tooling |

## 4.3 Regra arquitetural central

```text
LLM não é fonte de verdade.
LLM propõe.
Parser normaliza.
Synthesizer deduplica.
Quality gate decide.
Telemetry mede.
O humano continua responsável pelo merge.
```

---

## 5. Fronteiras permanentes de segurança

O projeto não deve:

```text
- usar CT102 como staging;
- chamar o runtime CT102 durante AgentReview;
- executar shell livre;
- executar SSH;
- executar docker exec;
- fazer deploy ou restart automático;
- executar comandos gerados por LLM;
- chamar provider de modelo diretamente;
- usar /v1/chat/ingest;
- expor Authorization, Bearer, cookies, tokens ou env real;
- publicar prompt ou payload bruto sensível;
- auto-aprovar PR;
- fazer auto-merge;
- transformar hipótese em P0/P1;
- aplicar suggested-contract-updates automaticamente;
- alterar regras médicas do AgentEscala fora de issue/PR própria;
- mover lógica de produto do AgentEscala para o AIOps.
```

### 5.1 Endpoint permitido para modelos

```text
/v1/chat/completions
```

### 5.2 Endpoint proibido

```text
/v1/chat/ingest
```

### 5.3 Ambiente canônico do AgentReview

```bash
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

Os CLIs devem falhar fechado quando esse boundary não é atendido.

---

## 6. Estado dos releases

## 6.1 `v0.19.0` — concluído

O release final consolidou:

- AgentReview Engine offline;
- AgentEscala thin-wrapper E2E no CT104;
- final review JSON/Markdown;
- environment boundaries CT104/CT102;
- transição controlada do runtime CT102;
- versão reportada pelo runtime `0.19.0`;
- `/health`, `/ready` e `/metrics` operacionais;
- database, providers e action catalog validados;
- backup app-scoped em `/mnt/aiops-backups`;
- rollback documentado e não utilizado;
- tag/release final publicada;
- issue #52 fechada como completed.

## 6.2 `v0.20.0` — em desenvolvimento

Issue mãe:

```text
#58 — release: v0.20.0 AgentReview Quality Gate track
```

Corte mínimo recomendado:

```text
#59 — deterministic review quality gate          CONCLUÍDA
#60 — quality gate E2E contract fixture          PRÓXIMA
```

Follow-ups do track:

```text
#61 — telemetry baseline
#62 — false-positive signatures / contract suggestions
#63 — optional second opinion contract
#64 — Validation Evidence semantic pre-review
#65 — AgentEscala wrapper consumption contract
```

### 6.3 Estratégia de release recomendada

`v0.20.0` deve ser tratado inicialmente como release de toolrepo/AgentReview. Não há necessidade de transição CT102 se o track não alterar o runtime produtivo.

Antes de tag final:

1. merge da #60;
2. contrato de consumo do gate documentado;
3. execução E2E no CT104;
4. canário no AgentEscala;
5. release notes atualizadas;
6. confirmação de que CT102 não foi modificado.

---

## 7. O que funciona hoje

## 7.1 Runtime CT102

Estado reportado e aceito no `v0.19.0`:

| Capacidade | Estado |
|---|---|
| Versão exposta | `0.19.0` |
| Health | OK |
| Readiness | OK |
| Metrics | OK |
| Database | OK |
| Provider registry | OK/estado esperado |
| Action catalog | OK |
| Audit store | Preservado |
| Approval/run stores | Preservados ou com baseline documentada |
| Backup | Criado |
| Rollback | Documentado; não utilizado |
| AgentReview tooling | Não instalado/executado no CT102 |

## 7.2 Environment guard

O projeto possui guard explícito para separar:

```text
prod/runtime/CT102
versus
dev/toolrepo/CT104
```

Os CLIs do AgentReview reutilizam essa política e bloqueiam execução em runtime produtivo.

## 7.3 Offline intake e redaction

Funciona hoje:

- leitura de `.aiops/repo-profile.yaml`;
- carregamento de artifacts declarados;
- status de artifact disponível/missing/invalid/degraded;
- redaction recursiva;
- relatório de redaction;
- bloqueio de escrita dentro do target repo;
- output sanitizado para estágios seguintes.

Artifacts:

```text
aiops-intake.json
redaction-report.json
```

## 7.4 Semantic chunk planner

Funciona hoje:

- agrupamento determinístico por papel semântico;
- até seis blocos inicialmente;
- cobertura complete/partial/degraded;
- arquivos cobertos, parcialmente cobertos e não cobertos;
- ordem e dependências explícitas.

Artifact:

```text
semantic-chunk-plan.json
```

Grupos canônicos:

```text
primary_backend_logic
api_schema_contract
frontend_ui
tests
workflow_aiops
docs_changelog
suspicious_out_of_scope
unknown
```

## 7.5 Structured chunk result parser

Funciona hoje:

- um JSON estruturado por chunk;
- validação de schema/chunk id/semantic group;
- normalização de findings;
- downgrade de observação especulativa para risk;
- rejeição de finding fora do chunk;
- rejeição/downgrade de evidência vazia ou placeholder;
- proteção contra test failure sem source confiável;
- dedupe determinístico;
- declaração de coverage e parse failures.

Artifact:

```text
chunk-results.json
```

## 7.6 Final review synthesizer

Funciona hoje:

- dedupe e ordenação de findings e risks;
- consolidação de limitations;
- comparação de coverage;
- contadores por severidade;
- veredito preliminar estruturado;
- Markdown em português para consumo humano;
- sanitização de secrets e paths absolutos.

Artifacts:

```text
final-review.json
final-review.md
```

## 7.7 Deterministic quality gate

Implementado e mergeado pela PR #66.

Funciona hoje:

- schema `agent-review.quality-gate.v1`;
- normalização de veredito;
- P0/P1 só confirmado com evidência mínima;
- `changes_requested` sem blocker confirmado vira `manual_review_required`;
- `approved` com blocker confirmado vira `changes_requested`;
- review partial/degraded não vira aprovação limpa;
- coverage crítica ausente pode exigir revisão manual;
- test failure exige source compatível;
- CT102/prod/deploy/restart exige evidência operacional explícita;
- placeholders, redaction e truncation-only não confirmam blocker;
- `quality_score` diagnóstico;
- segunda opinião mantida desabilitada;
- output determinístico e sanitizado;
- output bloqueado se sobrescrever input ou entrar no target repo.

Artifact:

```text
review-quality-gate.json
```

Campos decisórios:

```text
status
normalized_verdict
manual_review_required
blocked_reasons
warnings
limitations
```

Campos de segunda opinião nesta fase:

```json
{
  "second_opinion_requested": false,
  "second_opinion_status": "not_required"
}
```

Validação mais recente reportada após o fix da PR #66:

```text
focused tests: 26 passed
full suite: 638 passed
compileall: passed
git diff --check: passed
GitHub Actions aiops-ci: success
```

---

## 8. O que ainda não funciona ponta a ponta

## 8.1 Quality gate ainda não faz parte do E2E oficial

Embora o gate exista e esteja testado isoladamente, o teste E2E oficial ainda precisa executar:

```text
intake
→ plan
→ fake chunk responses
→ parse
→ synthesize
→ quality gate
```

Essa é a #60.

Sem essa PR, há uma lacuna entre:

- capacidade isolada validada;
- contrato de integração E2E reproduzível.

## 8.2 AgentEscala ainda não consome o gate como sinal canônico

O AgentEscala já foi validado como thin-wrapper para o fluxo até `final-review.json/md`, mas ainda precisa de PR própria para:

- chamar `aiops-review-quality-gate.py`;
- publicar/uploadar `review-quality-gate.json`;
- interpretar `status`, `normalized_verdict` e `manual_review_required`;
- falhar conservadoramente se o gate não existir ou for inválido.

## 8.3 Telemetry ainda não existe

A #61 deve gerar:

```text
review-telemetry.json
```

Ainda faltam métricas estruturadas de:

- gate status/verdict;
- blockers rebaixados;
- coverage;
- parse failures;
- false-positive markers;
- duração e tamanho dos bundles;
- histórico por PR/commit.

## 8.4 False-positive learning ainda não existe

A #62 deve adicionar:

```text
false-positive-signatures.json
suggested-contract-updates.yaml
```

Regras permanentes:

- sugestões separadas;
- revisão humana;
- nunca aplicar contratos automaticamente.

## 8.5 Segunda opinião ainda não existe

A #63 deve ser opt-in e só usar:

```text
/v1/chat/completions
```

Ela nunca deve:

- auto-aprovar;
- substituir o gate;
- decidir merge sozinha;
- usar provider direto;
- usar CT102.

## 8.6 Validation Evidence semantic pre-review ainda não foi integrada

A #64 deve tratar o pre-review como sinal, não verdade.

Regras:

- finding ambíguo vira risk/limitation;
- finding só sobe de severidade com prova;
- dedupe com final review;
- quality gate decide depois;
- fallback degraded honesto.

---

## 9. Manual operacional do AgentReview no CT104

## 9.1 Pré-condições

- checkout do target repo;
- checkout pinado do `aiops-orchestrator`;
- artifacts do target repo em diretório temporário;
- ambiente CT104/toolrepo;
- outputs fora do target repo;
- Python e dependências de desenvolvimento disponíveis.

Exemplo de diretórios:

```bash
TARGET_REPO="$GITHUB_WORKSPACE"
AGENT_DIR="$RUNNER_TEMP/agent"
AIOPS_TOOL_REPO="$RUNNER_TEMP/aiops-orchestrator"
```

## 9.2 Intake e redaction

```bash
python "$AIOPS_TOOL_REPO/scripts/aiops-review-intake.py" \
  --target-repo mglpsw/AgentEscala \
  --repo-root "$TARGET_REPO" \
  --agent-dir "$AGENT_DIR" \
  --output "$AGENT_DIR/aiops-intake.json" \
  --redaction-report "$AGENT_DIR/redaction-report.json"
```

## 9.3 Planejamento semântico

```bash
python "$AIOPS_TOOL_REPO/scripts/aiops-review-plan-chunks.py" \
  --intake "$AGENT_DIR/aiops-intake.json" \
  --output "$AGENT_DIR/semantic-chunk-plan.json"
```

## 9.4 Respostas estruturadas por chunk

Para cada chunk do plano, gerar:

```text
$AGENT_DIR/chunk-responses/<chunk_id>.json
```

A geração pode ser simulada em testes ou feita pelo workflow do target repo. Quando houver modelo, a chamada passa pelo Agent Router via `/v1/chat/completions`. Os CLIs determinísticos do AIOps não chamam modelo diretamente.

## 9.5 Parsing

```bash
python "$AIOPS_TOOL_REPO/scripts/aiops-review-parse-chunks.py" \
  --chunk-plan "$AGENT_DIR/semantic-chunk-plan.json" \
  --responses-dir "$AGENT_DIR/chunk-responses" \
  --intake "$AGENT_DIR/aiops-intake.json" \
  --output "$AGENT_DIR/chunk-results.json"
```

## 9.6 Síntese

```bash
python "$AIOPS_TOOL_REPO/scripts/aiops-review-synthesize.py" \
  --chunk-results "$AGENT_DIR/chunk-results.json" \
  --intake "$AGENT_DIR/aiops-intake.json" \
  --chunk-plan "$AGENT_DIR/semantic-chunk-plan.json" \
  --redaction-report "$AGENT_DIR/redaction-report.json" \
  --output-json "$AGENT_DIR/final-review.json" \
  --output-md "$AGENT_DIR/final-review.md"
```

## 9.7 Quality gate

```bash
python "$AIOPS_TOOL_REPO/scripts/aiops-review-quality-gate.py" \
  --final-review "$AGENT_DIR/final-review.json" \
  --chunk-results "$AGENT_DIR/chunk-results.json" \
  --intake "$AGENT_DIR/aiops-intake.json" \
  --chunk-plan "$AGENT_DIR/semantic-chunk-plan.json" \
  --redaction-report "$AGENT_DIR/redaction-report.json" \
  --output "$AGENT_DIR/review-quality-gate.json"
```

Para PR explicitamente crítica:

```bash
  --critical-pr
```

## 9.8 Semântica de exit code — ponto crítico

O wrapper não deve interpretar `exit code 0` como aprovação.

```text
exit 1
= falha de tooling, environment ou input; normalmente não há gate utilizável

exit 0
= artifact do gate foi produzido
= o gate ainda pode ter status=failed ou manual_review_required
```

O wrapper deve sempre ler:

```text
status
normalized_verdict
manual_review_required
```

## 9.9 Política de artifacts permitidos

Permitidos:

```text
aiops-intake.json
redaction-report.json
semantic-chunk-plan.json
chunk-results.json
final-review.json
final-review.md
review-quality-gate.json
sanitized diagnostics
```

Não publicar:

```text
full.diff bruto
prompts brutos
Router payload bruto
Router response não validada
headers
env dump real
tokens
cookies
secrets
```

---

## 10. Contrato de severidade e veredito

## 10.1 Severidades

```text
P0 = risco crítico comprovado
P1 = bug de alto impacto comprovado
P2 = problema real/provável que exige follow-up
P3 = melhoria, manutenção ou clareza
Risk = hipótese não confirmada
Limitation = falta de contexto/evidência/cobertura
```

## 10.2 Requisitos de P0/P1

Um blocker confiável exige:

- `file_path`;
- evidência concreta;
- impacto;
- `source_artifact` ou `line_or_hunk`;
- source chunk processado;
- ausência de placeholder/redaction-only/truncation-only;
- source confiável em claims de teste/operação.

## 10.3 Vereditos

```text
approved
approve_with_minor_notes
approve_with_required_followup
changes_requested
manual_review_required
review_unavailable
```

## 10.4 Regras do gate

```text
P0/P1 confiável presente
→ changes_requested

changes_requested sem P0/P1 confiável
→ manual_review_required

review partial/degraded sem blocker confiável
→ manual_review_required

P2 ou risk relevante
→ approve_with_required_followup

P3/limitation não crítica
→ approve_with_minor_notes

material insuficiente
→ review_unavailable
```

---

## 11. Inventário de issues e roadmap

| Issue | Tema | Estado atual | Próxima ação |
|---|---|---|---|
| #29 | Target repo profile/context | Concluída/superseded pela arquitetura nova | Manter fechada |
| #37 | Artifact loaders | Concluída | Manter contrato |
| #38 | Structured chunk parser | Concluída | Evoluir apenas por bug/compatibilidade |
| #39 | Final synthesizer | Concluída | Gate é autoridade posterior |
| #40 | Thin-wrapper contract | Concluída | Evoluir consumo do gate em #65/AgentEscala |
| #41 | Semantic grouping | Concluída | Ajustes futuros por telemetry |
| #42 | Telemetry/FP learning épico | Aberta | Executar #61 e #62 |
| #43 | Quality gate/second opinion épico | Parcial: gate concluído | Executar #60 e depois #63 |
| #44 | Validation Evidence pre-review | Aberta | Executar depois do gate/telemetry |
| #45 | Redaction/coverage deterministic | Aberta para reavaliação | Verificar saldo após #59/#60 |
| #46 | Roadmap mãe | Aberta | Atualizar checkpoints |
| #52 | Release v0.19.0 | Fechada/completed | Arquivar evidência |
| #58 | Release track v0.20.0 | Aberta | Coordenar #60–#65 |
| #59 | Deterministic quality gate | Concluída via PR #66 | Fechar/confirmar fechamento automático |
| #60 | Quality gate E2E fixture | Próxima | Implementar agora |
| #61 | Telemetry baseline | Aberta | Após #60 ou em paralelo controlado |
| #62 | FP signatures/contract suggestions | Aberta | Após #61 |
| #63 | Optional second opinion | Aberta | Depois do gate estabilizado |
| #64 | Validation Evidence integration | Aberta | Depois de gate/telemetry |
| #65 | AgentEscala gate consumption contract | Aberta | Após #60; pode ser acelerada |

---

## 12. Achados de auditoria e dívida técnica

## 12.1 P2 — E2E do quality gate ainda ausente

**Impacto:** o artifact existe e está testado isoladamente, mas o contrato completo não prova todos os passos em uma única execução reproduzível.

**Correção:** implementar #60.

## 12.2 P2 — AgentEscala ainda não usa o gate

**Impacto:** o ganho de redução de falso positivo ainda não chega ao comentário/decisão do target repo.

**Correção:** #65 + PR própria no AgentEscala.

## 12.3 P2 — documentação do release `v0.19.0` pode estar obsoleta

O arquivo de release consultado ainda continha linguagem de “release final não criado” e checklist aberto, apesar de o release já ter sido concluído.

**Risco:** operadores novos podem interpretar o estado incorretamente.

**Correção recomendada:** uma PR documental pequena para:

- marcar `v0.19.0` como final;
- registrar data/evidência;
- fechar checklist;
- apontar #52 e release final;
- separar histórico de plano futuro.

## 12.4 P2 — evidence index ainda é mínimo

O evidence index atual funciona principalmente como lista sanitizada de referências de artifacts. Ele ainda não representa completamente:

- fatos;
- riscos;
- limitations;
- confiança;
- proveniência detalhada;
- relações finding ↔ hunk ↔ check ↔ contrato.

**Correção:** evoluir incrementalmente antes ou durante #64, sem bloquear #60.

## 12.5 P2 — ausência de telemetry

Sem #61, decisões de melhoria ainda dependem de observação manual. Não há base histórica para quantificar:

- taxa de manual review;
- falsos P0/P1;
- coverage gaps;
- artifacts frequentemente ausentes;
- eficácia do gate.

## 12.6 P3 — `quality_gate.py` concentra muita lógica

O arquivo é grande e combina loaders, avaliação, score, sanitização e helpers. Isso não bloqueia o release, mas pode dificultar manutenção.

Refatoração futura possível:

```text
evidence_policy.py
coverage_policy.py
verdict_policy.py
quality_score.py
```

Não fazer essa refatoração dentro da #60.

## 12.7 P3 — `quality_score` poderia ter constraint de schema

O evaluator mantém o score em `[0, 1]`, mas o modelo pode ser reforçado com constraint explícita, por exemplo `Field(ge=0.0, le=1.0)`.

Não é blocker; registrar para hardening posterior.

## 12.8 P3 — checks são pouco interpretados pelo gate

O gate valida JSON e usa provenance/source para claims de teste, mas ainda não correlaciona profundamente o conteúdo de `checks.json` com cada finding.

Essa evolução deve ser feita com schema versionado e testes, evitando acoplamento a um único formato de CI.

---

## 13. Riscos principais

| Risco | Probabilidade | Impacto | Mitigação |
|---|---:|---:|---|
| Falso blocker P0/P1 | Média | Alto | Evidence gate, parser e quality gate |
| Aprovação limpa em review degradado | Baixa após #59 | Alto | `manual_review_required` |
| Secret em artifact/comment | Baixa | Crítico | Redaction + artifact allowlist |
| Target repo modificado pelo toolrepo | Baixa | Alto | Write guards + E2E snapshot na #60 |
| CT102 usado acidentalmente | Baixa | Crítico | Environment guard fail-closed |
| Wrapper tratar exit 0 como aprovação | Média | Alto | Ler campos do gate; testes no AgentEscala |
| Docs divergirem do código | Média | Médio | PR documental e source-of-truth hierarchy |
| AgentEscala duplicar engine | Média | Médio/alto | #65 e thin-wrapper contract |
| Segunda opinião aumentar ruído | Média | Médio | Opt-in, bundle mínimo, gate conservador |
| Sugestão alterar contratos automaticamente | Baixa | Alto | Sugestões separadas, nunca aplicadas |

---

## 14. Roadmap recomendado a partir de agora

## Fase imediata — fechar o contrato do gate

```text
#60 — Quality Gate E2E Contract Fixture
```

Entregas:

- E2E com sete artifacts;
- snapshot do target repo antes/depois;
- validação de sanitização;
- docs atualizadas;
- full suite verde.

## Fase de ativação no target repo

```text
#65 — AgentEscala wrapper consumption contract
→ PR própria no AgentEscala
```

Entregas:

- chamada do gate após synthesize;
- upload do artifact;
- mapping de resultado;
- fallback conservador;
- canário CT104.

## Fase de observabilidade

```text
#61 — review telemetry baseline
```

## Fase de aprendizado controlado

```text
#62 — false-positive signatures e contract suggestions
```

## Fase de revisão adicional opcional

```text
#63 — second opinion contract
```

## Fase de pre-review semântico

```text
#64 — Validation Evidence semantic pre-review
```

---

## 15. Passos rápidos para funcionar no AgentEscala

Esta é a trilha mais curta e segura para gerar valor real sem esperar telemetry/second opinion.

## 15.1 Passo 1 — mergear a #60

Garantir que o E2E oficial gere:

```text
review-quality-gate.json
```

## 15.2 Passo 2 — pin do toolrepo

No workflow do AgentEscala, fazer checkout do AIOps por SHA/tag aprovado, nunca por branch flutuante em produção do workflow.

Exemplo conceitual:

```yaml
- name: Checkout AIOps tool repo
  uses: actions/checkout@v4
  with:
    repository: mglpsw/aiops-orchestrator
    ref: <SHA-ou-tag-aprovado>
    path: ${{ runner.temp }}/aiops-orchestrator
```

## 15.3 Passo 3 — adicionar a chamada do gate

Depois do synthesize:

```bash
python "$RUNNER_TEMP/aiops-orchestrator/scripts/aiops-review-quality-gate.py" \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json" \
  --output "$RUNNER_TEMP/agent/review-quality-gate.json"
```

Usar `--critical-pr` somente com regra explícita do target profile/workflow.

## 15.4 Passo 4 — interpretar o artifact, não apenas exit code

Pseudocódigo do wrapper:

```python
if cli_failed or artifact_missing_or_invalid:
    publish_manual_review_required("quality_gate_unavailable")
elif gate["status"] == "failed":
    publish_review_unavailable(gate)
elif gate["manual_review_required"]:
    publish_manual_review_required(gate)
elif gate["normalized_verdict"] == "changes_requested":
    publish_final_review_with_blockers(gate)
else:
    publish_final_review_with_gate_status(gate)
```

O wrapper não deve:

- auto-aprovar;
- auto-mergear;
- mudar labels de aprovação automaticamente sem issue própria;
- executar ação operacional.

## 15.5 Passo 5 — upload permitido

Adicionar ao artifact upload:

```text
review-quality-gate.json
```

Nunca publicar artifacts brutos proibidos.

## 15.6 Passo 6 — comentário final mínimo

Adicionar ao comentário do AgentEscala uma seção curta:

```markdown
## Quality Gate

- Status: `passed|degraded|failed|manual_review_required`
- Normalized verdict: `...`
- Manual review required: `true|false`
- Quality score: `...` — diagnóstico, não decisão de merge
- Blocked reasons: `...`
- Limitations: `...`
```

## 15.7 Passo 7 — canário no CT104

Executar inicialmente em uma PR de baixo risco:

- sem alterações médicas críticas;
- com artifacts completos;
- sem CT102;
- sem deploy;
- comparar `final-review.json` com `review-quality-gate.json`;
- confirmar que o wrapper não confunde exit code com aprovação.

## 15.8 Passo 8 — cenários mínimos de canário

1. Review limpo → `approved` ou notes.
2. P1 confiável → `changes_requested`.
3. P1 com `[REDACTED]` → `manual_review_required`, não blocker.
4. Chunk ausente → `manual_review_required`.
5. Gate ausente/inválido → fallback conservador.
6. Referência a CT102 em guardrail/docs → não virar blocker automático.

## 15.9 Passo 9 — só depois ativar como sinal padrão

Critério para ativação padrão no AgentEscala:

- #60 verde;
- PR wrapper verde;
- pelo menos um canário;
- artifacts sanitizados;
- fallback validado;
- nenhum uso de CT102/provider direto/`/v1/chat/ingest`.

---

## 16. Definition of Done do `v0.20.0`

Corte mínimo:

```text
[ ] #59 mergeada e issue fechada
[ ] #60 mergeada
[ ] E2E gera 7 artifacts
[ ] review-quality-gate.json validado
[ ] target repo fixture não é modificado
[ ] full suite verde
[ ] docs E2E/Engine atualizadas
[ ] release notes v0.20.0
[ ] CT102 não alterado
```

Corte recomendado para valor no AgentEscala:

```text
[ ] #65 concluída
[ ] PR própria no AgentEscala mergeada
[ ] toolrepo pinado por SHA/tag
[ ] canário CT104 validado
[ ] fallback manual_review_required validado
```

Não bloquear `v0.20.0` mínimo por:

```text
telemetry completa
second opinion
Validation Evidence semantic pre-review
dashboard
persistência em banco
```

---

## 17. Governança recomendada

### 17.1 Uma issue executável por PR

- PR pequena;
- escopo fechado;
- critérios de aceite verificáveis;
- sem misturar runtime CT102 e AgentReview CT104.

### 17.2 Mudança no AgentEscala somente em PR própria

O AIOps pode documentar o contrato, mas não deve alterar silenciosamente o target repo.

### 17.3 Versionar schemas

Qualquer breaking change deve criar nova versão de schema, não reinterpretar silenciosamente `v1`.

### 17.4 Outputs determinísticos

- `sort_keys=True`;
- timestamps derivados de inputs quando necessário;
- sem paths absolutos;
- sem secrets;
- reason codes estáveis.

### 17.5 Evidência antes de severidade

Nenhum P0/P1 sem:

```text
arquivo + evidência + impacto + source confiável
```

---

## 18. Melhorias rápidas recomendadas além da #60

1. Atualizar `docs/RELEASE_V0_19_0.md` para estado final.
2. Confirmar fechamento automático da #59 após merge da #66.
3. Atualizar #58 e #46 com checkpoint pós-#66.
4. Reavaliar #45 depois da #60 e fechar o que foi absorvido.
5. Adicionar constraint `[0,1]` ao `quality_score` em PR de hardening futura.
6. Criar `docs/AIOPS_PROJECT_MANUAL.md` a partir deste documento e mantê-lo versionado.
7. Adicionar uma tabela de compatibilidade de schemas/artifacts por release.
8. Definir regra explícita de `critical-pr` no target profile, em vez de inferência informal.
9. Criar fixture de `checks.json` versionada antes de aprofundar correlação de test failure.
10. Depois da integração AgentEscala, priorizar #61 para medir o comportamento real do gate.

---

## 19. Referências oficiais

- [Release v0.19.0](https://github.com/mglpsw/aiops-orchestrator/releases/tag/v0.19.0)
- [Issue #46 — Evidence-Gated Multi-Block Review Control Plane](https://github.com/mglpsw/aiops-orchestrator/issues/46)
- [Issue #52 — release v0.19.0](https://github.com/mglpsw/aiops-orchestrator/issues/52)
- [Issue #58 — release track v0.20.0](https://github.com/mglpsw/aiops-orchestrator/issues/58)
- [Issue #60 — Quality Gate E2E Contract Fixture](https://github.com/mglpsw/aiops-orchestrator/issues/60)
- [PR #66 — deterministic review quality gate](https://github.com/mglpsw/aiops-orchestrator/pull/66)
- [Issue #61 — telemetry baseline](https://github.com/mglpsw/aiops-orchestrator/issues/61)
- [Issue #62 — false-positive signatures](https://github.com/mglpsw/aiops-orchestrator/issues/62)
- [Issue #63 — optional second opinion](https://github.com/mglpsw/aiops-orchestrator/issues/63)
- [Issue #64 — Validation Evidence semantic pre-review](https://github.com/mglpsw/aiops-orchestrator/issues/64)
- [Issue #65 — AgentEscala wrapper gate contract](https://github.com/mglpsw/aiops-orchestrator/issues/65)

---

## 20. Conclusão oficial

O projeto AIOps está em bom estado estrutural. O runtime produtivo foi estabilizado no `v0.19.0`, e o AgentReview já possui uma cadeia determinística madura até o quality gate. A arquitetura está corretamente separada entre CT104, CT102, AgentEscala e Agent Router.

O risco principal agora não é falta de engine; é falta de **integração oficial do último estágio**. A prioridade deve ser transformar o gate recém-mergeado em parte do E2E e consumi-lo no AgentEscala sem duplicar lógica.

A sequência recomendada é:

```text
#60
→ #65
→ PR AgentEscala
→ canário CT104
→ #61 telemetry
→ #62 learning controlado
→ #63/#64 quando houver dados reais
```

Essa ordem entrega valor rápido, reduz falsos positivos e preserva todas as fronteiras operacionais do projeto.
