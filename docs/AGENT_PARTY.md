# Prometheus — RPG Agent Party

Este playbook define como Claude Code, Codex e Copilot devem atuar no Prometheus quando a tarefa envolver análise, desenvolvimento, TDD, review, build, commit ou deploy.

O tema de RPG é só uma camada de coordenação. As responsabilidades abaixo são operacionais e não substituem `CLAUDE.md`, `TASKS.md`, as decisões D1-D5, a barreira `work` nem as proibições universais.

## Regra central: TDD-first

Toda mudança de código produtivo começa com uma estratégia de teste automatizado.

Antes de editar implementação, o agente deve identificar:

1. comportamento esperado;
2. teste automatizado mais próximo;
3. tipo de teste adequado: unitário, integração, CLI, regressão, contrato ou smoke;
4. falha esperada antes da correção, quando isso for viável.

Testes escritos só depois da implementação são exceção. Use essa exceção apenas quando o código legado não tiver harness claro ou quando a primeira ação segura for criar characterization tests. Nesse caso, registre a justificativa no handoff.

## Party padrão

| Classe | Função | Entrega principal |
| --- | --- | --- |
| Ranger | Exploração do codebase, busca semântica, tracing de dependências e impacto | Mapa de arquivos, fluxos e riscos |
| Sage | Análise técnica, arquitetura, tradeoffs e plano de abordagem | Decisão técnica clara e escopo |
| Paladin | TDD, critérios de aceite, testes regressivos e invariantes | Testes antes da implementação e validação final |
| Artificer | Implementação, refactor e integração | Código mínimo que faz os testes passarem |
| Inquisitor | Review crítico, bugs, segurança, regressões e aderência às regras | Lista de riscos e correções necessárias |
| Blacksmith | Build, lint, formatação, empacotamento e checks locais | Sinal verde de qualidade local |
| Courier | Commit, PR, changelog, handoff, deploy e checks pós-merge | Entrega rastreável e auditável |

Um único agente pode assumir múltiplas classes em sequência, mas deve declarar o papel ativo quando isso ajudar a manter o fluxo claro.

## Fluxo obrigatório

1. **Ranger** mapeia o código antes de qualquer alteração relevante.
2. **Sage** define a abordagem e limita o escopo.
3. **Paladin** transforma o objetivo em testes automatizados.
4. **Artificer** implementa o menor código que faz os testes passarem.
5. **Paladin** roda a suíte relevante e amplia cobertura se aparecer comportamento não coberto.
6. **Inquisitor** revisa riscos, regressões, segurança e regras do Prometheus.
7. **Blacksmith** roda build, lint e checks finais.
8. **Courier** prepara commit, PR, handoff ou deploy quando solicitado.

Para tarefas pequenas, combine etapas sem pular o gate TDD. Exemplo: "Ranger/Sage para entender, Paladin para teste de regressão, Artificer para correção mínima, Paladin/Blacksmith para validar".

## Classes

### Ranger

Use quando a tarefa exigir entender a área afetada.

Responsabilidades:

- buscar arquivos com `rtk grep`, `rtk find`, `rtk read` ou `pb search`;
- identificar entrypoints, dependências, contratos e testes existentes;
- apontar riscos de isolamento, especialmente `work`, `.ctxguard` e collections restritas;
- evitar conclusões arquiteturais antes de ler o código.

Entrega mínima:

- arquivos ou módulos afetados;
- testes existentes que cobrem a área;
- lacunas de cobertura relevantes.

### Sage

Use para decidir abordagem antes de editar.

Responsabilidades:

- traduzir objetivo em comportamento observável;
- escolher o menor escopo que resolve o problema;
- alinhar com D1-D5, stack e convenções do projeto;
- decidir quando o risco exige integração, CLI ou teste de contrato.

Entrega mínima:

- abordagem escolhida;
- comportamento fora de escopo;
- riscos que o Paladin precisa cobrir.

### Paladin

Use sempre antes e depois de implementação.

Responsabilidades:

- definir critérios de aceite testáveis;
- escrever ou atualizar testes antes de código produtivo quando viável;
- reproduzir bug com teste falhando antes da correção;
- criar characterization tests antes de refactor em área pouco coberta;
- validar que a suíte relevante passa após a implementação.

Regras por tipo de trabalho:

- **Bugfix:** teste de regressão primeiro, confirmar falha, corrigir, confirmar verde.
- **Feature:** testes para fluxo principal e edge cases relevantes antes da implementação completa.
- **Refactor:** garantir comportamento coberto antes; se não houver cobertura, caracterizar primeiro.
- **Docs/config sem código executável:** usar verificação automatizável quando existir; caso contrário, fazer revisão estrutural e explicar por que não há teste executável.

Entrega mínima:

- lista de testes criados ou reutilizados;
- comando de validação;
- resultado esperado e resultado observado.

### Artificer

Use para alterar implementação.

Responsabilidades:

- editar somente depois do plano de teste do Paladin;
- implementar a menor mudança coerente;
- preferir padrões existentes do repo;
- não introduzir dependências fora do `pyproject.toml`;
- preservar type hints, async em I/O e `dataclass` quando adequado.

Entrega mínima:

- mudança de comportamento implementada;
- arquivos tocados;
- qualquer dívida técnica deixada explicitamente fora de escopo.

### Inquisitor

Use para review antes de considerar pronto.

Responsabilidades:

- procurar bug, regressão, risco de segurança e quebra de contrato;
- checar se a barreira `work` continua intacta;
- verificar se falhas de teste/hook foram investigadas, não silenciadas;
- questionar testes frágeis, mocks indevidos e cobertura ilusória.

Entrega mínima:

- findings ordenados por severidade;
- decisão: bloquear, ajustar ou liberar.

### Blacksmith

Use para validação local.

Responsabilidades:

- rodar comandos com `rtk`;
- escolher o menor check suficiente para o risco da mudança;
- escalar para suíte completa quando a mudança tocar contratos compartilhados;
- reportar comandos que não puderam ser executados.

Comandos padrão:

```bash
rtk pytest
rtk ruff check
rtk python -m compileall src tests
```

Entrega mínima:

- comandos executados;
- resultado;
- falhas e causa raiz quando houver.

### Courier

Use para fechar entrega quando o usuário pedir commit, PR, deploy ou handoff.

Responsabilidades:

- revisar `rtk git status` antes de adicionar arquivos;
- nunca incluir `.env`, tokens, credenciais ou dados de cliente;
- separar mudanças próprias de mudanças pré-existentes do usuário;
- escrever commit/PR com resumo, testes e riscos;
- acompanhar checks pós-merge ou deploy quando aplicável.

Entrega mínima:

- arquivos incluídos;
- mensagem de commit ou descrição de PR;
- status dos checks/deploy.

## Handoffs

Cada handoff entre classes deve preservar estas informações:

- objetivo;
- arquivos afetados;
- testes planejados ou executados;
- riscos em aberto;
- comandos relevantes;
- decisão de continuar, ajustar ou bloquear.

## Regras de conflito

Em caso de conflito, a prioridade é:

1. proibições universais e barreira `work`;
2. decisões D1-D5;
3. `TASKS.md`;
4. este playbook;
5. preferência local do agente.

Nunca use o tema RPG como justificativa para pular teste, ignorar falha, ampliar escopo ou acessar contexto restrito.
