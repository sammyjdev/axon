# dec-107 — Estratégia de validação via GitHub Actions

- Status: accepted
- Date: 2026-05-25

## Context

Até este branch o repositório **não tinha CI configurado**: nenhum
`.github/workflows/*.yml`, nenhum pre-commit hook. Validação dependia de
quem rodava `pytest` localmente antes de pushar. Com a introdução do
profile system (dec-106), há agora **dois caminhos de execução**
(`AXON_PROVIDER_PROFILE=free|paid`) que precisam ser validados em cada
mudança — risco aumenta sem CI.

## Decision

Criar `.github/workflows/ci.yml` com 4 jobs paralelos, rodando em PR e em
push pra `master`/`main`:

| Job | O que faz | Por quê |
| --- | --- | --- |
| `lint` | `ruff check src/ tests/` | Pega problemas de estilo e bugs comuns antes do test runner pegar |
| `compile` | `python3 -m compileall src` | Pega erros de sintaxe em <2s, sem instalar deps |
| `test` | `pytest tests/router tests/resilience` em matriz `[py3.11, py3.12] × [free, paid]` | Garante que ambos os profiles funcionam e que o tier downgrade é profile-agnostic |
| `profile-smoke` | Importa `axon.router.engine`, verifica `_MODEL_MAP` resolvido pelo profile | Detecta breakage do registro de profile sem precisar rodar pytest inteiro |

### Escopo de testes em CI

CI roda **apenas** `tests/router` e `tests/resilience` por agora. Justificativa:

- São os módulos diretamente afetados pelo dec-106
- Garantia clara de que ambos os profiles passam
- Resto do repo tem dependências de infra (Qdrant, Redis, mem0,
  tree-sitter-java) que exigem mais setup

Suíte mais ampla será incluída em CI à medida que cada área for verificada
estável em runner Ubuntu (issue separada por área).

### Concorrência

`concurrency.cancel-in-progress: true` cancela runs antigos quando um novo
commit chega ao mesmo branch. Economiza minutos de runner em iteração
rápida de PR.

### Cache

`actions/setup-python@v5` com `cache: pip` e
`cache-dependency-path: pyproject.toml` corta ~80% do tempo de install em
runs subsequentes.

## Rationale

- **Profile matrix é não-negociável.** A regressão mais provável depois do
  dec-106 é "alguém muda model string e quebra um profile sem perceber".
  Matriz captura isso em todo PR.
- **Python 3.11 + 3.12 matrix.** `pyproject.toml` declara `>=3.11`; CI
  deve provar isso, não confiar.
- **Workflow começa enxuto.** 4 jobs simples são preferíveis a um pipeline
  complexo que ninguém entende. Cresce conforme necessidade.
- **dec-102 preservado.** Workflow não adiciona surface de configuração
  ao router; só valida o que existe.

## Consequences

- **Lint debt zero como pré-requisito.** UP042 pré-existentes em
  `policy/core.py`, `circuit_breaker.py`, `expansion/budget.py`,
  `expansion/scoring.py` migrados pra `StrEnum`. Fixtures do chunker
  (ADR-005/D5) excluídas via `per-file-ignores`.
- **Workflow não pode ser pushado via OAuth sem scope `workflow`.** Este
  branch versiona o arquivo em `docs/ci-workflow-proposed.yml`. Promoção
  pra `.github/workflows/ci.yml` precisa de push manual pelo owner do
  repo (ver Migration abaixo).
- **Sem secrets em CI por enquanto.** Profile FREE precisa de
  `GROQ_API_KEY` e `NVIDIA_NIM_API_KEY` pra fazer calls reais, mas os
  testes de `test_router` e `test_resilience` mockam essas calls — não
  precisam de keys. Se um dia quisermos um job de smoke real contra Groq,
  aí adiciona secret.
- **Rate limit gate é testável sem rede.** `test_classifier_raises_when_rate_limited`
  mocka `_RATE_LIMITER` e verifica `DENY_RATE_LIMIT` antes de qualquer
  chamada LiteLLM. Coberto.

## Out-of-scope

- **Coverage.** `pytest-cov` está em `[dev]` mas não rodamos cobertura em
  CI por agora. Pode ser adicionado quando houver uma meta clara (ex.: 80%
  em `src/axon/router/`).
- **Tests de integração com providers reais.** Bater no Groq de CI exige
  secret + budget. Vale quando o produto estabilizar.
- **Pre-commit hooks.** CI cobre o caso. Hooks são opcionais e responsabilidade
  do dev local.
- **Validação de docs (link check, etc.).** Não vale o custo de manutenção
  agora.

## Migration

O arquivo `.github/workflows/ci.yml` precisa ser movido pra a localização
final manualmente:

```bash
git checkout claude/routing-strategy-validation-lyQBT
mkdir -p .github/workflows
cp docs/ci-workflow-proposed.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: enable workflow from dec-107"
git push
```

Pushes futuros do workflow exigem token com scope `workflow` ou push
direto do owner do repo. Agentes operando via OAuth standard não
conseguem promover.

Após o primeiro push manual, edições subsequentes do mesmo arquivo já
funcionam via push normal — o gate é só na criação inicial em alguns
fluxos de OAuth.
