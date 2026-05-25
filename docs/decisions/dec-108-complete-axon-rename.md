# dec-108 — Complete Prometheus → AXON rename (closes dec-100 followups)

- Status: accepted
- Date: 2026-05-25

## Context

dec-100 (2026-05-21) renomeou Python package, distribuição e CLI de Prometheus
pra AXON. Três consequências foram deixadas como follow-up explícito:

1. `prometheus.toml` não foi renomeado pra `axon.toml`
2. `PROMETHEUS_*` env vars sem compat fallback (já tinha sido removido)
3. Path local `~/dev/Prometheus` não renomeado (a cargo do operador)

Além disso, o sweep mecânico de dec-100 deixou ~250 referências residuais
"Prometheus"/"prometheus" em docstrings, help text, configs e tests.

## Decision

Finalizar o rename completo do dec-100 nesta única passada:

- **Cat A — texto/display**: docstrings, mensagens echo, help text, READMEs.
  `s/Prometheus/AXON/` em contextos de display.
- **Cat B — identificadores**:
  - `get_prometheus_config_path()` → `get_axon_config_path()`
  - `prometheus.toml` → `axon.toml`
  - `~/dev/Prometheus` → `~/dev/axon` (default em `.env.example` e `runtime.py`)
  - Test function names referenciando `prometheus_toml` renomeados
  - Serialization kind `"config/prometheus_toml"` → `"config/axon_toml"` no
    exporter e em testes correlatos
- **Cat C — infra externa**:
  - `POSTGRES_USER` default em `docker-compose.yml` e `.env.example`:
    `prometheus` → `axon`
  - User-Agent HTTP `PrometheusExpansion/1.0` → `AxonExpansion/1.0`

### Compat shim pra `prometheus.toml` legado

`get_axon_config_path()` em `src/axon/config/runtime.py` agora:

1. Honra `AXON_CONFIG` (env override) se setado
2. Default novo: `./axon.toml`
3. **Fallback**: se `./axon.toml` não existir mas `./prometheus.toml` sim,
   lê o legado silenciosamente

Sem warning duro pra não poluir CLI; migração é `mv prometheus.toml axon.toml`
quando o usuário quiser.

### O que NÃO foi renomeado

- `docs/decisions/dec-*.md` (histórico)
- `docs/superpowers/specs/2026-05-18-prometheus-second-brain-design.md`
  (spec histórica do design original)
- `src/axon/store/graph_namespace.py` e seu teste — dead code marcado em
  dec-101 (Neo4j dropado); namespace literal `"prometheus"` no teste
  preservado por irrelevância
- Volumes Postgres já criados sob user `prometheus` em instalações
  existentes (compat via env var explícita)

## Rationale

- Inconsistência entre código e nome do projeto polui onboarding e
  documentação de tier 1.
- `prometheus.toml` era o último ponto de fricção real do rename — o resto
  era cosmético mas vazio de valor.
- Compat shim resolve migração local sem forçar o usuário a renomear o
  arquivo no mesmo PR que pulled.

## Consequences

- Usuários com `prometheus.toml` local continuam funcionando. Quando
  renomearem manualmente pra `axon.toml`, o shim sai do caminho.
- Volumes Postgres com `POSTGRES_USER=prometheus` já provisionados
  continuam funcionando se `.env.local` mantiver o override; o default
  novo é `axon` mas o env override prevalece.
- Trace logs / observabilidade que filtram por User-Agent
  `PrometheusExpansion` precisam atualizar pra `AxonExpansion`.
- Test fixtures do chunker (`tests/embedder/fixtures/python/router.py`)
  preservadas intactas (ADR-005/D5).

## Migration

Para usuários existentes:

```bash
git pull
# Se voce tem prometheus.toml local, o engine continua lendo. Quando
# quiser, renomeie pra axon.toml:
mv prometheus.toml axon.toml

# Se voce tinha .env.local apontando AXON_ENGINE=~/dev/Prometheus, esse
# path continua valido — o repo nao depende do nome do diretorio local.
# Renomear o diretorio e' opcional.

# Se voce subiu Postgres com user 'prometheus' anteriormente, mantenha o
# .env.local com POSTGRES_USER=prometheus. Instalacoes novas ja pegam
# 'axon' como default.
```

dec-100 fica fechado a partir desta decisão; follow-ups de rename caem
nesta dec.
