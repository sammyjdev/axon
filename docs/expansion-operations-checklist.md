# Expansion Operations Checklist

## Objetivo
Operar o pipeline de expansao com seguranca, baixo custo e revisao humana obrigatoria.

## Principios
- Local-first por padrao.
- Cloud e opcional e so deve ser usado quando houver ambiguidade real.
- Nada vai para o vault final sem `approve`.
- `work` continua explicito e manual.

## Fase 1: Ativacao
Objetivo: validar o fluxo ponta a ponta com seguranca.

### Pre-requisitos
- Configurar `PROMETHEUS_EXPANSION_SOURCE_CATALOG`.
- Confirmar catalogo de fontes para `knowledge`, `career` e `personal`.
- Comecar com cloud desligado.

### Execucao
Para cada contexto abaixo, rodar um teste manual:
- `pb expand run --ctx knowledge --topic "<tema>" --fast`
- `pb expand run --ctx career --topic "<tema>" --fast`
- `pb expand run --ctx personal --topic "<tema>" --fast`

Depois de cada `run`:
1. Revisar o arquivo gerado com `pb expand review <arquivo_staging>`
2. Decidir:
3. Aprovar: `pb expand approve <arquivo_staging>`
4. Rejeitar: `pb expand reject <arquivo_staging>`

### Criterio de saida
- 3 execucoes completas sem erro.
- Todo output inicial vai para staging.
- Publicacao ocorre apenas via `approve`.
- Nenhuma escrita indevida fora do vault esperado.

## Fase 2: Operacao
Objetivo: transformar o fluxo em rotina leve e previsivel.

### Cadencia
- Rodar 2 a 4 vezes por semana.
- Usar `--fast` como padrao.
- Usar cloud apenas quando necessario.

### Rotina por execucao
1. Escolher um tema.
2. Rodar `pb expand run --ctx <contexto> --topic "<tema>" --fast`.
3. Revisar com `pb expand review <arquivo_staging>`.
4. Aprovar ou rejeitar.
5. Confirmar se o publish ocorreu corretamente apos `approve`.

### Criterio de saida
- Taxa de aprovacao estavel.
- Backlog de staging baixo.
- Tempo por execucao previsivel.
- Uso de cloud raro e intencional.

## Fase 3: Governanca
Objetivo: manter qualidade alta e custo controlado.

### Rotina semanal
1. Contar quantos drafts foram aprovados.
2. Contar quantos drafts foram rejeitados por baixa evidencia.
3. Verificar pendencias em staging.
4. Verificar consumo cloud acumulado no mes.
5. Ajustar volume ou fontes se a qualidade cair.

### Regras de ajuste
- Aprovacao baixa: reduzir fontes e apertar selecao de temas.
- Rejeicao alta por baixa evidencia: revisar catalogo de fontes.
- Cloud subindo: voltar para local-only.
- Muito draft pendente: reduzir frequencia de runs.

### Criterio de saida
- Pipeline estavel por 2 a 3 semanas.
- Custo dentro do teto mensal.
- Qualidade consistente dos drafts aprovados.

## Checklist rapido semanal
- [ ] Rodei expansao nos contextos prioritarios da semana.
- [ ] Revisei todos os drafts novos.
- [ ] Aprovei apenas conteudo com evidencia suficiente.
- [ ] Rejeitei drafts fracos sem tocar o vault final.
- [ ] Verifiquei backlog de staging.
- [ ] Verifiquei uso cloud do mes.
- [ ] Ajustei fontes ou frequencia se necessario.

