# Contributing — AIOps Orchestrator

Este documento descreve as diretrizes de contribuição para o `aiops-orchestrator` dentro da
organização KS-SM Labs.

---

## Fluxo de branches

- Crie uma branch a partir de `main` com um nome descritivo:
  - `feat/<descricao>` — nova funcionalidade
  - `fix/<descricao>` — correção de bug
  - `docs/<descricao>` — documentação
  - `chore/<descricao>` — tarefas de manutenção
- Mantenha branches com escopo pequeno e focadas em um único objetivo.
- Remova branches já mergeadas.

---

## Pull Request obrigatório para `main`

- **Todo merge para `main` deve ser feito via Pull Request.** Push direto na `main` não é
  permitido.
- O PR deve ter título claro e descrição do que foi alterado e por quê.
- Utilize o template disponível em `.github/pull_request_template.md`.
- PRs que envolvam automações operacionais, execução remota, shell commands, Docker, SSH ou
  deploy exigem revisão extra antes do merge.

---

## Testes antes do merge

- Execute os testes antes de abrir o PR:

  ```bash
  pip install -r requirements-dev.txt
  pytest tests/ -v
  ```

- Certifique-se de que o CI (GitHub Actions) está verde antes de solicitar revisão.
- Não desative ou remova testes existentes sem justificativa documentada no PR.

---

## Não versionar secrets

- **Nunca** adicione tokens, chaves de API, senhas, certificados ou endpoints sensíveis ao
  repositório.
- Utilize o arquivo `.env` (não versionado) para variáveis de ambiente locais, seguindo o
  exemplo em `.env.example`.
- Se detectar um secret no histórico, notifique imediatamente os mantenedores.

---

## Não alterar produção sem aprovação

- Alterações em configurações de produção (deploy, Docker Compose, variáveis de ambiente
  canônicas, configurações do CT 102) exigem aprovação explícita de um mantenedor.
- Não execute comandos remotos, SSH ou ações destrutivas sem aprovação documentada.
- Toda ação que afeta o ambiente de produção deve ter um runbook ou documentação associada.

---

## Mudanças de automação operacional

- Automações operacionais devem ser **idempotentes**.
- Ações destrutivas devem exigir **confirmação explícita**.
- Priorize o modo **dry-run** sempre que aplicável.
- Separe claramente **diagnóstico** de **remediação**.
- Não acople o repositório a caminhos locais sem documentar.
- Mudanças em automações operacionais passam por revisão cuidadosa antes do merge.

---

## Dúvidas

Abra uma issue ou mencione `@core-maintainers` no PR.
