# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Linhagem e Arquitetura de Conhecimento — AgentReview v2

**Campanha:** `aocm-repository-reconciliation`  
**Projeto:** AgentReview (Geração 2)  
**Status Atual:** `DEVELOPMENT | SHADOW_ADOPTION` (Linha sucessora em desenvolvimento)

---

## 1. Princípios e Fundamentos do AgentReview v2

O AgentReview v2 foi projetado para resolver os limites arquiteturais do v1:
1. **Exact Subject Binding:** Ancoragem criptográfica em commit SHA exato de 40 caracteres com autoridade de objeto confiável (`trusted_object_authority_v2`).
2. **Component-wise No-Follow Descent:** Todo acesso ao repositório ou storage externo valida componente por componente, recusando travessias, links simbólicos e special files (FIFO, sockets).
3. **Contratos e Schemas Formais:** Todos os contratos são JSON Schemas versionados e reprodutíveis (`schemas/agent-review/v2/`), exportados a partir de `contracts_v2.py`.
4. **Verificação Causal e Hash Binding:** O manifesto vincula o hash do payload ao hash da resposta do chunk.
5. **Trusted Checks em Executor Isolado:** Checks necessários (CI, lint) são produzidos por executores isolados com proveniência atestada, sem confiar em claims cegas do autor da PR.
6. **Decisão Canônica de Prontidão:** `ReviewReadinessV2` é a única autoridade canônica pós-síntese no v2.

---

## 2. Trilhas e Componentes Arquiteturais

### 2.1 Autoridade de Objeto e Subject Exato
- `git_commit_subject_v2.py`: Resolve e congela o commit subject exato.
- `trusted_object_authority_v2.py`: Garante que objetos Git (commits, trees, blobs) venham do repositório autorizado, sem confiar em ponteiros voláteis.
- `external_path_ingress_v2.py`: Centraliza a validação de caminhos absolutos e bounds de filesystem.
- `bounded_git_v2.py`: Envolve invocações Git em orçamentos estritos de tempo e saída.

### 2.2 Extração de Conteúdo e DLP
- `diff_acquisition_v2.py`: Adquire diffs sem risco de bloqueio infinito em arquivos especiais.
- `review_content_v2.py` / `review_content_extraction_v2.py`: Extrai o contexto real dos hunks com janelamento lossless.
- `dlp_v2.py`: Aplica políticas declarativas de Data Loss Prevention para evitar vazamento de segredos para backends LLM.

### 2.3 Montagem de Payloads e Transporte
- `manifest_v2.py`: Constrói o manifesto canônico da revisão.
- `payload_set_v2.py` / `payload_builder_v2.py`: Empacota os semantic chunks com metadados e referências cruzadas.
- `review_transport_v2.py`: Gerencia o transporte de chunks com verificação de recibo único do Router (`_router_receipt_v2.py`).

### 2.4 Checks Confiáveis e Executor Isolado
- `trusted_checks_v2.py` / `trusted_check_supervisor_v2.py`: Coordena a execução de checks necessários.
- `isolated_executor_v2.py`: Executa comandos em namespaces isolados e controlados pelo host.
- `required_check_provenance_v2.py` / `required_check_readiness_v2.py`: Conecta os resultados de checks confiáveis à árvore de decisão de prontidão.

### 2.5 Síntese e Decisão de Prontidão
- `synthesis_v2.py`: Sintetiza achados e riscos normalizados com rastreabilidade de passagens.
- `readiness_decision_v2.py`: Emite `ReviewReadinessV2` com invariantes estritos (ex: P3-only findings nunca bloqueiam `ready`).
- `lifecycle_v2.py`: Gerencia o ciclo de vida estrito dos achados conforme `FindingDispositionV2` (`new`, `confirmed`, `fixed`, `dismissed`, `superseded`, `stale`; o estado `RESOLVED` não existe no schema).

### 2.6 Target Pack e Distribuição Multi-target
- `target_pack_*.py`: Empacota o AgentReview v2 para instalação limpa em repositórios-alvo (`AgentEscala`, `InterLeitos`).
- Comandos implementados: `init`, `doctor`.
- Comandos pendentes: `validate`, `conformance`, `apply`, `rollback`.

---

## 3. Matriz de Classificação dos Componentes do v2

| Componente | Classificação | Status e Contexto |
|---|---|---|
| Trust Object Authority | v2 canonical/current | Integrado em `master` via PR #312, #313, #322, #341. |
| Contratos e Schemas v2 | v2 canonical/current | 22 schemas JSON exportados e verificados em CI. |
| Extração de Hunks e DLP | v2 canonical/current | Shipped em `v0.22.0`. |
| Isolated Executor & Simulator | v2 canonical/current | Shipped em `v0.22.0`. |
| Provenance Bridge & Readiness Wiring | v2 canonical/current | Integrado em `master` via PR #220. |
| Benchmark Harness (9 casos) | v2 canonical/current | Integrado em `evals/agent_review_v2/` e verificado em CI. |
| Target Pack (init / doctor) | v2 canonical/current | Integrado em `master` via PR #228, #230, #247. |
| Target Pack (validate / apply / rollback) | v2 planned | Planejado sob épico #199 e slice #203. |
| Dual-shadow / Rollout ativo | v2 planned | Rollout mode atual configurado como `off`. |
| PR #318 (forensic attempt) | v2 superseded attempt | Fired architectural STOP; sucedido por PR #312. |
| PR #309 (G2C egress branch) | v2 failure corpus | Fired STOP_G2C; branch preservada como evidência forense. |
| PR #327 (Ledger linter candidate) | v2 unresolved handoff | Mantido como corpus candidato sob issue #324 / #329. |

---

## 4. Caminho Crítico e Próximos Marcos

1. Fechamento formal de `#200`, `#201` e `#202` aguarda validação em alvos reais (canários semânticos no AgentEscala).
2. A slice `#203` (Target Pack completo com validação de conformance) é o próximo marco de implementação.
3. Promoção para `default` ou `required` em repositórios-alvo exigirá grants humanos específicos e observação em modo shadow.
