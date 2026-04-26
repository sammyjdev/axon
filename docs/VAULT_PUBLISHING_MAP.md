# Prometheus — Mapa de Publicação no Vault

Documento de referência: o que vai para onde no Obsidian vault, por contexto. Define fonte primária, formato esperado, política de sincronização e critérios de manutenção.

**Regra geral:** o vault é destino de conteúdo operacional e curado — não é espelho do repositório. Docs de engine (specs, ADRs do repositório, TASKS) ficam no repositório; conteúdo de conhecimento e operação do dia-a-dia vai para o vault.

---

## Contexto `knowledge/`

### O que vai para cá

| Conteúdo                        | Path no vault                              | Criado por                                       | Formato         |
| ------------------------------- | ------------------------------------------ | ------------------------------------------------ | --------------- |
| TIL (aprendizado rápido do dia) | `knowledge/daily/YYYY-MM-DD/til-SLUG.md`   | `pb til "..."`                                   | Template TIL    |
| HOW-TO (receita reproduzível)   | `knowledge/daily/YYYY-MM-DD/howto-SLUG.md` | Promoção automática via `pb til --promote-today` | Template HOW-TO |
| Referência técnica              | `knowledge/deep/TEMA.md`                   | Manual                                           | Markdown livre  |

### Não publicar aqui

- Decisões arquiteturais do projeto → vai em `adrs/`
- Notas de carreira ou entrevistas → vai em `career/`
- Specs ou backlogs de projetos → vai em `personal/<projeto>/`

### Templates

**TIL** (`knowledge/daily/YYYY-MM-DD/til-SLUG.md`):

```markdown
---
tags: [TECNOLOGIA, SUBTAG]
created: YYYY-MM-DD
type: til
promoted: false
---

# TIL: TÍTULO EM UMA LINHA

DESCRIÇÃO EM 2-3 FRASES. O que é, por que importa.

<bloco de código mínimo opcional>

Contexto: onde/como descobri isso.
```

**HOW-TO** (`knowledge/daily/YYYY-MM-DD/howto-SLUG.md`):

```markdown
---
tags: [TECNOLOGIA, SUBTAG]
created: YYYY-MM-DD
type: howto
verified: true
promoted_from: til-YYYY-MM-DD
---

# HOW-TO: TÍTULO ACIONÁVEL

## Quando usar

Contexto específico onde essa solução se aplica.
Quando NÃO usar (igualmente importante).

## Dependências

<dependências e pré-requisitos>

## Código mínimo

<implementação mínima que funciona>

## Armadilha principal

O erro mais comum e como evitar.

## Usado em

- [[projeto/arquivo]] — contexto de uso real
```

### Ciclo de vida

```
pb til "..." → knowledge/daily/YYYY-MM-DD/til-SLUG.md (promoted: false)
↓ (critério: snippet + pitfall + context)
pb til --promote-today → howto-SLUG.md (promoted: true no TIL original)
```

---

## Contexto `career/`

### O que vai para cá

| Conteúdo                            | Path no vault                  | Criado por                               |
| ----------------------------------- | ------------------------------ | ---------------------------------------- |
| Brief de empresa para entrevista    | `career/empresas/EMPRESA.md`   | `pb career brief "EMPRESA"` ou manual    |
| Perguntas e respostas de entrevista | `career/entrevistas/TOPICO.md` | `pb career interview "TOPICO"` ou manual |
| Métricas compiladas                 | `career/metricas.md`           | `pb career metrics`                      |
| Metas e estratégia de carreira      | `career/strategy.md`           | Manual                                   |

### Template de empresa

```markdown
# [EMPRESA]

## O que fazem

Uma linha do negócio principal.

## Stack técnica

Tecnologias relevantes que usam.

## Por que aplicar

O que me atrai nessa empresa.

## Perguntas para fazer

- ...

## Notas de processo seletivo

- Data: YYYY-MM-DD — fase: ...
```

### Template de tópico de entrevista

```markdown
# [TÓPICO]

## Resumo de 30s

Uma frase para abrir qualquer resposta sobre este tópico.

## Experiências relevantes (STAR)

### [Empresa/Projeto] — [Data]

**S:** ...
**T:** ...
**A:** ...
**R:** ...

## Perguntas técnicas comuns e respostas

### P: ...

R: ...
```

---

## Contexto `personal/`

### O que vai para cá

| Conteúdo                    | Path no vault                                | Formato             |
| --------------------------- | -------------------------------------------- | ------------------- |
| Contexto de projeto pessoal | `personal/<PROJETO>/CONTEXT.md`              | Template CONTEXT.md |
| ADRs do projeto             | `personal/<PROJETO>/adrs/YYYY-MM-DD-SLUG.md` | Template ADR        |
| Notas e decisões pessoais   | `personal/notas/YYYY-MM-DD-SLUG.md`          | Markdown livre      |

### Template CONTEXT.md por projeto

```markdown
# [Nome do Projeto]

## O que é

Uma linha descrevendo o projeto.

## Stack

- Backend:
- Frontend:
- Infra:
- LLMs (se aplicável):

## Estado atual

O que está funcionando hoje. O que está em progresso.

## Próximos passos

Lista priorizada do que precisa ser feito.

## Decisões importantes já tomadas

Links para ADRs relevantes ou resumo das decisões que não podem ser revertidas.

## O que NÃO fazer

Abordagens já tentadas e descartadas, com motivo.

## Como rodar localmente

<comandos mínimos para subir o projeto>

## Para entrevistas

Como descrevo esse projeto em 2-3 frases com métricas.
```

### Criar via CLI (ADRs de projetos pessoais)

```bash
pb adr add --project aerus-rpg --title "escolha de banco de dados"
pb adr list --project aerus-rpg
```

---

## Contexto `adrs/`

### O que vai para cá

Decisões arquiteturais de projetos **que merecem contexto histórico de longo prazo** — diferente dos ADRs inline em `personal/<PROJETO>/adrs/`.

| Conteúdo                                       | Path no vault                        |
| ---------------------------------------------- | ------------------------------------ |
| ADRs cross-projeto (afetam múltiplos projetos) | `adrs/YYYY-MM-DD-SLUG.md`            |
| ADRs do próprio Prometheus (engine)            | `adrs/prometheus/YYYY-MM-DD-SLUG.md` |

> Os ADRs do repositório do engine (`docs/ADR.md`) são a fonte primária de decisões do Prometheus. O vault pode receber versões curadas ou resumidas para consulta rápida, mas `docs/ADR.md` é authoritative.

### Template ADR

```markdown
# ADR-XXX: [Título]

**Data:** YYYY-MM-DD  
**Projeto:** [nome]  
**Status:** Aceito | Supersedido por ADR-YYY | Em revisão

## Contexto

Por que essa decisão precisou ser tomada. O que estava acontecendo.

## Decisão

O que foi decidido, de forma clara e sem ambiguidade.

## Justificativa

Por que essa opção e não as alternativas consideradas.

## Alternativas descartadas

| Alternativa | Motivo do descarte |
| ----------- | ------------------ |
| ...         | ...                |

## Consequências

O que muda. O que fica mais fácil. O que fica mais difícil.

## Revisão

Quando revisar esta decisão (trigger ou data).
```

---

## Contexto `work/` (restrito)

O contexto `work` **não faz parte deste plano de publicação**. Qualquer operação nele requer `--ctx work` explícito e está fora do escopo desta documentação por proteção de propriedade intelectual (Avangrid).

---

## Política de sincronização

| Tipo de conteúdo          | Fonte primária        | Vault recebe                          | Frequência            |
| ------------------------- | --------------------- | ------------------------------------- | --------------------- |
| TIL / HOW-TO              | Vault (criado direto) | —                                     | Tempo real (watcher)  |
| ADRs de projetos          | Vault (`pb adr add`)  | —                                     | Imediato após decisão |
| ADRs do engine Prometheus | `docs/ADR.md`         | Versão curada opcional                | Sob demanda           |
| Guias operacionais        | Repositório engine    | Não espelhar — consultar via `pb ask` | —                     |
| Specs históricas          | `docs/archive/`       | Nunca                                 | —                     |

**Regra de ouro:** Se é operação de usuário (o que fazer, como usar o vault), vai para o vault. Se é implementação do engine (como funciona por dentro, specs), fica no repositório.

---

## Critérios de pronto para publicação (checklist por documento)

Antes de publicar qualquer documento no vault, verificar:

- [ ] Não contém referências a tools externas como se fossem nativas (`codex exec`)
- [ ] Comandos `pb` citados existem na implementação atual
- [ ] Estado do projeto descrito é consistente com MVP completo (abril 2026)
- [ ] Não contém caminhos ou referências a `~/vault/work/` sem aviso de restrição
- [ ] Front-matter YAML válido se for TIL ou HOW-TO (`tags`, `created`, `type`)
- [ ] Não contém credenciais, tokens ou dados proprietários
- [ ] Links internos (`[[...]]`) apontam para arquivos que existem no vault
