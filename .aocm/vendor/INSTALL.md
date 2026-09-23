# Instalação, adoção e atualização

## Requisitos

Python 3.11 ou posterior e Git no PATH. A ferramenta usa apenas a biblioteca padrão. A qualificação desta entrega especifica o ambiente realmente testado; isso não declara suporte executado em todos os sistemas. A suíte de desenvolvimento usa `jsonschema`; `requirements-dev.txt` fixa a versão do ambiente observado.

Em Windows, substitua `python` por `py -3` quando esse for seu launcher. Caminhos com espaços devem ficar entre aspas. Os comandos abaixo são invocações do script distribuído, não de um pacote registrado em PyPI.

## Passos

1. Extraia a entrega em uma pasta comum. Confirme o SHA-256 da entrega pelo arquivo externo de checksums, quando sua origem for confiável.
2. Execute a verificação. Para vincular a um valor externo esperado, passe `--expect-manifest SHA256_DO_MANIFEST` ao `verify`.
3. Execute `install --repo CAMINHO` sem `--apply`: não haverá escrita.
4. Execute com `--apply` para criar `.aocm/`. O diretório de destino deve ser a raiz real de um repositório Git, não um subdiretório nem um symlink.
5. Leia `.aocm/ADOPTION.md`; preencha `.aocm/repository-profile.json` a partir de fontes reais e obtenha adjudicação do mantenedor. `UNADOPTED` não concede autoridade.
6. Forneça `.aocm/ENTRYPOINT.md` ao agente. Para carregamento recorrente por sua ferramenta, integre manualmente esse caminho ao mecanismo de instruções já existente nela. O instalador não modifica instruções preexistentes.

## Árvore instalada

```
.aocm/
  vendor/                     # cópia do pack com manifest verificável
  repository-profile.json     # perfil local a adjudicar
  ENTRYPOINT.md               # referência ao único owner do método
  ADOPTION.md
  INSTALL_RECEIPT.json         # instalação, não qualificação do produto
```

## Não sobrescrever é parte do contrato

Uma `.aocm` existente, mesmo vazia ou symlink, faz a instalação recusar sem alterar esse destino. Não há `--force`. Não há `update`, `uninstall`, network updater, merge ou deploy nesta versão. Atualizações futuras devem comparar versão, perfis locais e delta de obrigações; não apague uma instalação existente para contornar a recusa.

A criação reserva o diretório via operação exclusiva. Falhas após a reserva deixam marcador de instalação incompleta, sem dizer que houve sucesso. O instalador não promete transação global, resistência a administrador hostil, rollback de modificações concorrentes ou atomicidade de filesystem em qualquer plataforma. Repositório, checkout e pasta da distribuição devem ficar sem mutadores concorrentes durante a execução.

## O que nunca é executado

Comandos encontrados em README, issues ou perfil; hooks Git; CI; instaladores do produto; credenciais; rede; banco; provider; marcação Ready; merge; release. `doctor` só observa a raiz Git e a instalação local. Capacidade de acessar o repositório não equivale a autorização.

## Uso sem instalação

Os documentos e prompts funcionam como contexto manual. O pacote não exige serviço, container, GPU, homelab, agente específico ou acesso externo para ler sua síntese. Revalidar fontes privadas e operar num repositório exige as permissões correspondentes; ausência de acesso não vira prova de inexistência.

## Limites do formato local

O bootstrap limita a distribuição a 2.048 membros, 8 MiB por arquivo e 64 MiB no total. São limites de recurso definidos pelo próprio pack, não constantes semânticas de qualquer repositório. Caminhos precisam ser relativos e portáveis; symlinks e arquivos especiais são recusados. Não use o instalador como extrator de archives arbitrários.
