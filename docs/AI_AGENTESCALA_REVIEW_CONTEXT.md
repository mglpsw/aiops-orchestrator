# Contexto de review para PRs do AgentEscala

## Papel do AgentEscala

AgentEscala é um sistema de escala médica. O risco principal não é apenas bug visual: é quebrar regra de negócio de plantões, cobertura, troca, exportação, auditoria ou notificações operacionais.

## Princípio central

A evolução deve ser incremental, auditável e segura.

Ordem de prioridade:

1. integridade da escala médica;
2. auditoria;
3. testes;
4. staging;
5. documentação;
6. produção apenas em janela controlada.

## Ambientes

- CT104: dev/staging.
- CT102: produção.
- CT102 não deve ser usado como staging.
- Não tocar CT102 sem autorização explícita.

## Contrato 24H/12H/10-22H

Estado consolidado pós-PR #224:

- Dia vazio mostra:
  - VAGO 24H;
  - VAGO 12H DIA independente;
  - VAGO 10-22H;
  - não mostra VAGO 12H NOITE por padrão.

- 24H ocupado:
  - aparece como card visual 24H;
  - cobre sua própria metade DIA e NOITE;
  - preserva 12H DIA independente quando aplicável;
  - não cria VAGO 12H NOITE falso;
  - não afeta 10-22H.

- 10-22H:
  - sempre independente;
  - nunca depende de 24H;
  - não deve ser `covered_by_24h`.

- 24H splitado:
  - pai permanece com `lifecycle_status="split"`;
  - filhos ficam `canonical_derived`;
  - filhos mantêm `parent_shift_id`;
  - shift 12H independente nunca deve ser adotado como filho de 24H.

## Frontend vs backend

- Backend trabalha com canônicos.
- Frontend agrupa visualmente.
- Frontend não deve recriar domínio indevidamente.
- Exportação/contabilidade continuam canônicas/divididas.
- Calendar é a interface operacional principal.

## Calendar-first

O Calendário é o caminho recomendado para:

- preencher plantões;
- editar plantões;
- dividir 24H;
- limpar plantões;
- acionar overlays operacionais.

Páginas Admin devem orientar, diagnosticar ou fazer manutenção, não competir com o calendário como fluxo primário.

## Admin > Escala

A página Admin > Escala deve:

- reforçar o fluxo calendar-first;
- evitar materialização canônica acidental;
- deixar ações legadas/técnicas colapsadas ou claramente avisadas;
- priorizar plantões extras fora da grade canônica;
- preservar handlers/API quando PR for UI-only.

## Notificações

Regra arquitetural:

> Audit events registram fatos. Notificações consomem fatos.

Notificações não devem alterar regra de escala.

WhatsApp/Pushover/NotificationProvider devem:

- ficar desabilitados por padrão;
- não fazer chamada externa em testes;
- não expor secrets;
- não tocar coverage/swap/fill/exportação;
- não alterar regra médica.

## Quando a PR for frontend-only

O reviewer deve proteger o escopo:

- não sugerir backend/migration sem bug real;
- não pedir refactor grande;
- não mexer em operational_slot_service;
- não alterar coverage/swap/exportação;
- não misturar WhatsApp/Pushover.

## Quando a PR tocar calendário, operational slots ou shift service

Exigir validação crítica:

```bash
docker exec agentescala_dev_backend python -m pytest \
  tests/test_operational_slots_phase15.py \
  tests/test_operational_slots_phase3.py \
  tests/test_calendar_phase16_fill_bugs.py -q

cd frontend && npx vitest run tests/calendar_page.test.jsx
```

## Quando a PR tocar Admin UI

Validação recomendada:

```bash
cd frontend
npm run build
npx vitest run tests/calendar_page.test.jsx
npx vitest run tests/*admin* || true
```

## Linhas vermelhas

Não permitir PR que:

- reintroduza slots sintéticos ocupados do 24H;
- adote 12H independente como filho de 24H;
- faça 10-22H depender de 24H;
- crie VAGO 24H fantasma em dia com 24H ocupado;
- altere coverage/swap/exportação em PR de UI;
- use produção como staging;
- faça chamadas externas reais em testes;
- vaze token/secret;
- altere regra médica para resolver bug visual.
