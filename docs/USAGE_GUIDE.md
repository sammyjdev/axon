# Guia de uso do Prometheus

## Antes de começar

A infra precisa estar rodando. Sem ela, nada funciona.

Ordem recomendada de setup em máquina nova:

1. `./setup.sh`
2. `pipx install --editable /Users/samdev/dev/Prometheus`
3. bootstrap do vault em `docs/VAULT_SETUP.md`
4. `pb index ~/vault/knowledge --ctx knowledge`

Checklist rápido antes de iniciar qualquer sessão: veja a seção **Checklist antes de uma sessão** ao final deste guia.

```bash
# Mac
docker compose --profile cpu up -d

# Verificar se o MCP está conectado (Claude Code)
claude mcp list   # deve mostrar: prometheus ✓ Connected

# Verificar se o CLI está instalado
pb --help
```

Se o LaunchAgent estiver configurado em `~/Library/LaunchAgents/dev.samdev.colima.plist`, o Colima e os containers sobem no boot sem precisar de nenhum comando.

---

## Claude Code

Claude Code tem acesso total ao vault via MCP. Ele chama as tools do servidor diretamente, sem precisar do terminal.

### Tools do MCP

| Tool                 | O que faz                                             |
| -------------------- | ----------------------------------------------------- |
| `search_code`        | busca semântica no vault (código, HOW-TOs, notas)     |
| `ask`                | query com detecção de contexto pelo cwd               |
| `get_session_memory` | resumo comprimido de sessões anteriores de um projeto |
| `get_dependencies`   | grafo de chamadas de uma classe ou função             |
| `get_adrs`           | lista ADRs do projeto                                 |
| `save_adr`           | persiste uma nova decisão                             |
| `get_memory`         | memória Mem0 filtrada por contexto                    |

O parâmetro `caller="claude-code"` é passado automaticamente. Isso libera 8000 tokens de retorno (vs. 2000 do Copilot), então Claude Code recebe respostas mais completas do vault.

Contextos válidos: `personal`, `career`, `knowledge`, `work`. Nunca use `ctx="work"` por engano — ele acessa código proprietário da Avangrid.

### Numa sessão típica

Claude Code detecta o contexto pelo cwd quando você abre um projeto. Para decisões arquiteturais que você quer registrar, peça `save_adr` durante a sessão. Para retomar trabalho de outra vez, `get_session_memory` traz o que foi discutido anteriormente.

### O que o Claude Code deve fazer antes de agir

Ler `CLAUDE.md` e `TASKS.md`. Se não houver task `status: open`, só agir com pedido explícito. TDD obrigatório. Nunca silenciar falha de teste. Nunca tocar `~/vault/work/` sem `ctx="work"`.

---

## GitHub Copilot

Copilot também tem acesso ao MCP, mas com budget menor: 2000 tokens por resposta. Funciona melhor para completions dentro de um arquivo aberto.

Ele é bom em completar boilerplate, sugerir imports consistentes com o `pyproject.toml` e seguir as convenções do projeto (dataclass, type hints, async). Não é o agente certo para refatorar múltiplos arquivos, tomar decisões de arquitetura ou tocar collections `work`.

Para trabalho multi-módulo ou qualquer coisa que envolva a barreira `work/`, use Claude Code.

---

## Cursor

O Cursor não tem integração nativa com o MCP do Prometheus. Para usá-lo com o vault, rode o `pb` no terminal integrado e cole o output no chat.

```bash
pb search "como resolver X" --ctx knowledge
pb ask "padrão usado em Spring services" --ctx knowledge
```

O output do `pb ask` funciona bem como contexto manual colado numa janela de chat.

---

## CLI `pb`

### Padrão Claude + Codex (TDD first)

Fluxo recomendado para tarefas de código: sempre escrever ou atualizar testes antes da implementação.

1. Recuperar contexto com Prometheus.
2. Gerar plano no Claude (planner).
3. Executar tasks no Codex em paralelo (executor).
4. Rodar testes e só então integrar.

> **Nota:** `codex exec` é uma ferramenta **externa ao Prometheus** (OpenAI Codex CLI). Não é um comando `pb`. O `pb ask` no passo 1 é o único comando Prometheus neste fluxo — os demais são chamadas a ferramentas externas.

Exemplo prático:

```bash
# 1) Contexto via Prometheus (comando pb nativo)
pb ask "implementar feature X" --cwd "$PWD" --rtk-engine auto

# 2) Planner no Claude (ferramenta externa)
claude --model claude-opus-4-7 -p "Gere plano em JSON com tasks paralelas, critérios de aceite e testes obrigatórios."

# 3) Executor no Codex (ferramenta externa — requer Codex CLI instalado)
codex exec -C "$PWD" --model o3 "Execute task_id=T1 com TDD first"
codex exec -C "$PWD" --model o3 "Execute task_id=T2 com TDD first"

# 4) Validação local
pytest -q
```

### Consultas

```bash
# Detecta contexto pelo cwd automaticamente
pb ask "como funciona Spring @Transactional com self-invocation"

# Forçar engine RTK interna/externa
pb ask "resumir contexto do módulo de autenticação" --rtk-engine external

# Busca semântica direta
pb search "UUID qdrant" --ctx knowledge

# Filtrar por linguagem
pb search "service layer" --ctx knowledge --lang java --top 10
```

### Indexação

```bash
# Indexação pontual
pb index ~/vault/knowledge --ctx knowledge
pb index ~/dev/meu-projeto --ctx personal

# Watcher: reindexa automaticamente ao salvar
pb watch ~/vault/knowledge --ctx knowledge
```

### Knowledge (TIL e HOW-TO)

```bash
# Registrar um aprendizado
pb til "Spring @Transactional não funciona em self-invocation" --tags java,spring

# Ver TILs que ainda não viraram HOW-TO
pb til --list

# Promover todos os TILs do dia
pb til --promote-today

# Converter um TIL específico
pb til howto --from knowledge/daily/2026-04-20/til-spring-transac.md
```

### ADRs

```bash
pb adr add --project prometheus --title "usar UUID5 no Qdrant"
pb adr list --project prometheus
```

### Sessão de contexto

```bash
pb session knowledge
pb session personal

# work pede confirmação interativa antes de liberar o acesso
pb session work
```

### Carreira

```bash
pb career brief "Nubank"       # brief da empresa para entrevista
pb career interview "kafka"    # experiências relevantes ao tópico
pb career metrics              # métricas compiladas do vault
```

### Aprofundamento e custo

```bash
pb deep suggest --ctx knowledge   # sugestões do que estudar a seguir
pb cost today
pb cost week --breakdown          # detalha por contexto
```

### RTK — compressão de contexto

O Prometheus possui dois modos de compressão de contexto (RTK):

#### RTK interno (sempre disponível, sem instalação)

Heurística local do Prometheus. Ativo por padrão em `pb ask` com `--rtk-engine internal`.

```bash
pb ask "resumir contexto do módulo de autenticação" --rtk-engine internal
```

#### RTK externo (opcional — requer instalação prévia)

> **Pré-requisito:** `brew install rtk` — binário externo não incluído no Prometheus.

```bash
# Instalar RTK externo
brew install rtk

# Inicializar integração para o agente desejado
pb rtk-init --agent claude
pb rtk-init --agent codex

# Verificar se RTK externo está disponível
pb rtk-status

# Usar RTK como proxy de comando
pb rtk-proxy "git status"

# Compressão direta com engine externa
pb rtk "$(cat README.md)" --engine external --max-tokens 300
```

Engines disponíveis:

| Engine     | Comportamento                                        | Requer instalação |
| ---------- | ---------------------------------------------------- | ----------------- |
| `auto`     | tenta RTK externo e cai para interno se indisponível | Não               |
| `internal` | usa apenas heurística local do Prometheus            | Não               |
| `external` | exige RTK externo instalado (`brew install rtk`)     | **Sim**           |

---

## Como funciona a detecção de contexto

Quando você não passa `--ctx`, o `pb ask` tenta inferir o contexto pelo cwd e pelo conteúdo da query.

| Cwd contém                   | Contexto inferido |
| ---------------------------- | ----------------- |
| `avangrid`, `vault/work`     | `work`            |
| `vault/career`               | `career`          |
| `vault/knowledge`            | `knowledge`       |
| `aerus-rpg`, `linkedin-tool` | `personal`        |

Palavras na query também contribuem: `vaga`, `salário`, `recruiter` apontam para `career`; `kafka`, `spring`, `qdrant` apontam para `knowledge`.

Nunca deixe o agente adivinhar `work`. Passe `--ctx work` de forma explícita quando precisar do contexto Avangrid.

---

## Coleções no Qdrant

| Coleção     | Conteúdo                             |
| ----------- | ------------------------------------ |
| `knowledge` | HOW-TOs, TILs, referências técnicas  |
| `career`    | prep de entrevistas, empresas, metas |
| `personal`  | projetos pessoais, decisões, notas   |
| `work`      | código Avangrid (acesso restrito)    |

Sem `--ctx`, as buscas cobrem `personal`, `career` e `knowledge`. A coleção `work` só entra com ctx explícito.

---

## Observabilidade

O Langfuse fica em [http://localhost:3000](http://localhost:3000) e mostra custo por modelo, latência e chamadas por contexto. Útil para ver onde o dinheiro está indo.

---

## Checklist antes de uma sessão

```bash
# Infra está up?
docker compose ps

# MCP conectado? (Claude Code)
claude mcp list

# Watcher está rodando?
ps aux | grep "pb watch"

# Indexar se necessário
pb index ~/vault/knowledge --ctx knowledge
```
