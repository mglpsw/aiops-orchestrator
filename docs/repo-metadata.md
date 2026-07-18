# Metadados do Repositório — aiops-orchestrator

## Nome do repositório

`aiops-orchestrator`

---

## Descrição recomendada

> Orquestrador AIOps para diagnóstico operacional, automações, análise de métricas e integração
> com agentes.

---

## Topics recomendados

`aiops`, `automation`, `observability`, `prometheus`, `agents`, `homelab`, `operations`,
`diagnostics`

---

## Função dentro da organização KS-SM Labs

Este repositório concentra a camada de orquestração AIOps do ecossistema KS-SM Labs:
diagnóstico assistido, automações operacionais e integração com métricas reais do homelab.

Por poder evoluir para executar ações operacionais sobre a infraestrutura, é tratado com
**cautela especial** — mais do que um repositório puramente documental.

---

## Times sugeridos

| Time               | Permissão |
|--------------------|-----------|
| `aiops-dev`        | Write     |
| `core-maintainers` | Maintain  |
| `read-only`        | Read      |

---

## Permissões recomendadas

- Membros externos: somente leitura via fork.
- Contribuidores: Write (via time `aiops-dev`).
- Mantenedores: Maintain (via time `core-maintainers`).
- Sem acesso Admin para contas individuais fora dos owners da organização.

---

## Branch padrão, release e proteção de `master`

- A branch padrão do repositório é `master`.
- Pull Requests de release devem usar `master` como base.
- Tags de release devem apontar somente para commits já integrados em `master`.
- A proteção de branch de `master` está habilitada no GitHub; force push e deleção estão
  desabilitados.

Proteções adicionais recomendadas:

- Exigir Pull Request antes de merge.
- Exigir pelo menos 1 aprovação de revisão.
- Exigir que status checks (CI) passem antes do merge.
- Exigir resolução de todas as conversas antes do merge.
- Bloquear force push na `master`.
- Restringir push direto na `master` (somente via PR).
- Exigir revisão extra para mudanças em automações, execução remota, shell commands, Docker,
  SSH ou deploy.

---

## Cuidados especiais

Por ser um orquestrador operacional, as seguintes práticas são obrigatórias:

1. **Idempotência**: toda automação operacional deve ser segura para reexecução.
2. **Confirmação explícita**: toda ação destrutiva deve exigir confirmação antes de executar.
3. **Runbook obrigatório**: não adicionar comandos de produção sem runbook ou documentação.
4. **Sem execução remota por padrão**: comandos remotos não devem ser executados automaticamente.
5. **Separação diagnóstico/remediação**: diagnóstico e remediação são fluxos distintos.
6. **Dry-run prioritário**: sempre que possível, implementar modo dry-run.
7. **Sem secrets no repositório**: tokens, chaves, senhas e endpoints sensíveis nunca devem
   ser versionados.
8. **Sem acoplamento a caminhos locais**: caminhos absolutos do ambiente devem ser
   documentados explicitamente.
