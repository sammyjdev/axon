# Expansion Weekly Tracking

## Objetivo
Registrar as execucoes reais da Fase 2 com um formato simples, repetivel e facil de revisar no fim da semana.

Arquivo base:
- [expansion-weekly-tracker.csv](/Users/samdev/dev/Prometheus/docs/expansion-weekly-tracker.csv)

## O que registrar por execucao
- `ctx`
- `topic`
- `risk_level`
- `recommended_action`
- `decision` final: `approve` ou `reject`
- `total_time_seconds`
- `cloud_cost_usd`
- `staging_file`
- `publish_file`, quando houver `approve`
- `notes`

## Fluxo recomendado
1. Escolher uma linha `planned` no CSV.
2. Rodar `pb expand run --ctx <ctx> --topic "<topic>" --fast`.
3. Rodar `pb expand review <arquivo_staging>`.
4. Decidir `approve` ou `reject`.
5. Atualizar a linha correspondente no CSV.

## Como medir rapido

### Tempo total
No terminal, rode com `time`:

```bash
time pb expand run --ctx knowledge --topic "python async testing patterns" --fast
```

Preencha `total_time_seconds` com o tempo total da execucao. Se preferir, use arredondamento simples.

### Risk level e recommended action
Vem da saida de:

```bash
pb expand review <arquivo_staging>
```

Campos esperados na saida:
- `risk_level=...`
- `recommended_action=...`

### Cloud cost por execucao
O custo por execucao fica na telemetria JSONL da expansion. A forma mais segura de conferir a ultima execucao e:

```bash
tail -n 1 ~/.prometheus/expansion/telemetry/executions.jsonl
```

Campos uteis:
- `cloud_cost_usd`
- `used_cloud`
- `ctx`
- `topic`
- `status`

Se quiser conferir em formato legivel:

```bash
python3 -m json.tool <<< "$(tail -n 1 ~/.prometheus/expansion/telemetry/executions.jsonl)"
```

## Regras de leitura no fim da semana
- Se a maioria sair com `risk_level=high` e `decision=reject`, reduza fontes ou ajuste temas.
- Se `cloud_cost_usd` continuar em `0.0`, mantenha cloud desligado por mais uma semana.
- Se `knowledge`, `career` ou `personal` aprovarem com consistencia, mantenha as fontes associadas.
- Se o backlog de `staging` crescer, reduza o numero de runs da semana seguinte.

## Tarefas da semana
- [ ] Executar 1 run em `knowledge`
- [ ] Executar 1 run em `career`
- [ ] Executar 1 run em `personal`
- [ ] Revisar todos os drafts novos
- [ ] Aprovar ou rejeitar sem deixar staging pendente
- [ ] Revisar o CSV no fim da semana
- [ ] Podar fontes com sinal baixo
