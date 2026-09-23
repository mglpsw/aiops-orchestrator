# AOCM-MPACK
## 0.1.0-preview.1 · pacote de método e bootstrap local

Um pacote portátil para **planejar, construir, revisar e encerrar mudanças com claims delimitadas, evidência rastreável e decisão humana**. Derivado de leituras do AOCM, CAEM/F1/Q1/RK-1 e SACR-AS/APCP v2 em 23/09/2026.

**É uma distribuição candidata independente.** Não é AOCM v2, implementação do CAEM, adapter CAEM oficial, conclusão de RK-1, execução de Q1 ou certificador universal de PRs. A adoção pelo repositório deve ser explícita. Fontes candidatas não foram promovidas a normas upstream.

### Começar

Leia `method/CORE.md`, depois `method/PR_LIFECYCLE.md`. Escolha somente os perfis pertinentes. Use `prompts/START.md` com seu agente. Para receber uma visão humana progressiva, comece por `method/GUIA_HUMANO.md`.

### Instalar — dentro da pasta extraída da entrega

```bash
python -B pack/tools/mpack.py verify --root pack
python -B pack/tools/mpack.py install --repo /caminho/do/repositorio
python -B pack/tools/mpack.py install --repo /caminho/do/repositorio --apply
python -B pack/tools/mpack.py doctor --repo /caminho/do/repositorio
```

O primeiro `install` apenas simula. A aplicação cria **somente `.aocm/`**, quando ainda não existe, sem sobrescrever `AGENTS.md`, `CLAUDE.md`, workflows, hooks, configurações Git ou produto. Nenhum comando de teste do repositório é executado. O resultado é `UNADOPTED`, não uma política aprovada. Leia `INSTALL.md`.

### Conteúdo e limites

| Superfície | O que entrega |
|---|---|
| `method/` | Core compacto, ciclo de PR, convergência, contratos semânticos e guia humano |
| `profiles/` | Regras selecionáveis por risco e superfície; nada autoativado |
| `templates/`, `schemas/` | Contratos locais MPACK, separados dos schemas upstream |
| `provenance/` | Fontes pinadas, registro de conhecimento, reconciliação e lacunas |
| `prompts/`, `agents/` | Entrada, planejamento, revisão, reparo e handoff sem duplicar o método |
| `examples/` | Exercícios sintéticos; não evidência de PRs reais |
| `tools/` | Verificação de bytes, instalação não destrutiva e inventário Git local |
| `tests/` | Controles positivos e negativos do próprio pacote |
| `chatgpt/` | Texto pronto para contexto de um projeto/conversa, sem integração automática |
| `research/` | Limites e trilhas não implementadas |

`PACKAGE_INTEGRITY_VALID` significa consistência do conjunto de bytes. Não significa origem autenticada, correção semântica, prontidão de PR ou autorização. A avaliação desta entrega está **fora do subject do pack**, em `../qualification/`.

Fonte de nomenclatura: **APCP v2 = Adversarial PR Construction Protocol**. “ACPC v2”, usado no pedido, foi reconciliado como referência a APCP v2; não foi criado um novo método com outra sigla. [S06]
