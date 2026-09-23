# Schemas locais

Esses schemas JSON pertencem ao MPACK, não ao schema_registry AOCM nem ao CAEM. Validam forma e alguns estados mecânicos; não interpretam prose, autenticam grants, provam adequação de fontes ou decidem semântica de evidência. Uma instância sintaticamente válida continua dependendo de adjudicação competente.

`templates/task.json` e `repository-profile.json` são esqueletos deliberadamente não adotados; campos vazios não são evidência. `observation.json` começa UNEXECUTED_TEMPLATE/NOT_EVALUATED. Resultados não pertencem à definição congelada da tarefa.

A ferramenta de instalação não executa um avaliador de PR baseado nesses schemas. A suíte de desenvolvimento usa a biblioteca JSON Schema indicada em `requirements-dev.txt` apenas para conferir os contratos e controles de forma incluídos.
