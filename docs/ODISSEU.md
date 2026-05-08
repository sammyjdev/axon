# Odisseu — Deep Agent sobre Prometheus

## Visão geral

Odisseu é um deep agent independente que consome Prometheus como backend
de contexto e memória. Não modifica o Prometheus — só o usa via MCP e CLI (`pb`).
Tem seu próprio loop de execução autônomo.

Recebe um goal em linguagem natural, decompõe em tasks com acceptance criteria
verificáveis, executa cada task usando ferramentas locais, verifica resultados,
aprende com os outcomes e persiste o conhecimento no vault do Prometheus.

Zero intervenção humana no loop de execução — humano define o goal e aprova o ship.

---

## Contexto: Prometheus

Prometheus é um context engine self-hosted em Python 3.11+ com:

- CLI `pb` (Typer), MCP server (FastMCP), Qdrant + Redis + Neo4j + SQLite
- Pipeline de compressão: caveman (Ollama phi3) → RTK binary
- Roteamento de modelos: Haiku/Sonnet/Opus por task type
- MCP tools: `ask`, `search_code`, `get_session_memory`, `get_dependencies`,
  `get_adrs`, `save_adr`, `get_graph_neighbors`, `get_graph_path`
- Env vars: `PROMETHEUS_ENGINE`, `PROMETHEUS_VAULT`, `PROMETHEUS_DAILY_BUDGET`

---

## Identidade do projeto

| Campo | Valor |
|---|---|
| Nome | Odisseu |
| CLI | `od` |
| Linguagem | Python 3.11+ |
| Repositório | projeto independente — não submódulo do Prometheus |

---

## Arquitetura

### Camadas

```
CLI `od`  (Typer)
    ↓
GoalOrchestrator          ← loop principal
    ↓              ↓
Planner         Learner   ← decompõe goal / captura outcomes
    ↓
TaskQueue                 ← lista ordenada de tasks com critérios
    ↓
Executor                  ← executa task via ferramentas
    ↓
Verifier                  ← verifica acceptance criteria
    ↓
PrometheusClient          ← contexto, memória, ADRs via MCP/pb
    ↓
ModelRouter               ← Ollama local | Claude API (fallback)
```

### Modelo de dados central

```python
@dataclass
class Goal:
    id: str             # uuid4
    description: str
    ctx: str            # prometheus context: knowledge|personal|career|work
    created_at: datetime
    status: GoalStatus  # pending|running|done|failed|aborted

@dataclass
class Task:
    id: str
    goal_id: str
    objective: str
    files: list[str]
    acceptance_criteria: list[str]
    tests: list[str]          # comandos para verificar (ex: pytest tests/foo.py)
    risk: str
    rollback: str
    dependencies: list[str]   # task_ids que devem completar antes
    status: TaskStatus        # pending|running|done|failed
    retries: int
    outcome: str | None

@dataclass
class ExecutionResult:
    task_id: str
    success: bool
    output: str
    error: str | None
    files_changed: list[str]
    criteria_met: list[bool]
```

---

## Stack

| Componente | Tecnologia |
|---|---|
| CLI | Typer 0.12+ |
| Async runtime | asyncio nativo |
| LLM local | Ollama via LiteLLM |
| LLM API fallback | Anthropic Claude via LiteLLM |
| Model primário | `ollama/qwen2.5-coder:7b` |
| Model fallback | `claude-sonnet-4-6` |
| Model arquitetura | `claude-opus-4-7` |
| Estado do agente | SQLite (aiosqlite) — goals + tasks |
| Prometheus context | subprocess pb / MCP stdio client |
| Execução shell | asyncio.subprocess |
| Config | TOML (tomllib nativo 3.11) |
| Testes | pytest + pytest-asyncio |
| Lint | Ruff |

---

## Estrutura de arquivos

```
odisseu/
├── pyproject.toml
├── CLAUDE.md
├── AGENTS.md                         # aponta para CLAUDE.md
├── .env.example
├── config/
│   └── agent.toml
├── docs/
│   ├── ADR.md
│   └── USAGE.md
├── src/
│   └── odisseu/
│       ├── __init__.py
│       ├── cli/
│       │   └── od.py                 # CLI principal (Typer)
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── orchestrator.py       # GoalOrchestrator — loop principal
│       │   ├── planner.py            # decompõe goal em tasks
│       │   ├── executor.py           # roda tasks via ferramentas
│       │   ├── verifier.py           # checa acceptance criteria
│       │   └── learner.py            # captura outcomes no vault
│       ├── models/
│       │   ├── __init__.py
│       │   ├── goal.py               # Goal, GoalStatus
│       │   ├── task.py               # Task, TaskStatus, ExecutionResult
│       │   └── plan.py               # Plan (goal + tasks ordenadas)
│       ├── store/
│       │   ├── __init__.py
│       │   └── agent_store.py        # SQLite — persiste goals e tasks
│       ├── prometheus/
│       │   ├── __init__.py
│       │   └── client.py             # PrometheusClient — wraps pb CLI + MCP
│       ├── router/
│       │   ├── __init__.py
│       │   └── model_router.py       # local vs API routing
│       └── config/
│           ├── __init__.py
│           └── runtime.py            # carrega agent.toml + env vars
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_planner.py
    ├── test_executor.py
    ├── test_verifier.py
    └── test_agent_store.py
```

---

## Módulos

### config/agent.toml

```toml
[model]
primary = "ollama/qwen2.5-coder:7b"
fallback = "claude-sonnet-4-6"
architecture = "claude-opus-4-7"
ollama_host = "http://localhost:11434"

[budget]
daily_usd = 2.0
local_first = true

[loop]
max_retries_per_task = 3
max_tasks_per_goal = 20
timeout_per_task_s = 120
require_ship_approval = true

[prometheus]
engine = ""         # sobrescrito por PROMETHEUS_ENGINE
vault = ""          # sobrescrito por PROMETHEUS_VAULT
mcp_mode = "cli"    # "cli" = subprocess pb | "mcp" = stdio MCP client

[learning]
auto_til = true
auto_adr = true
confidence_threshold = 0.8
```

### config/runtime.py

Carrega `agent.toml` via tomllib + variáveis de ambiente:

- `ODISSEU_PRIMARY_MODEL`
- `ODISSEU_DAILY_BUDGET`
- `PROMETHEUS_ENGINE` (obrigatório)
- `PROMETHEUS_VAULT`
- `ANTHROPIC_API_KEY` (opcional — só para fallback)
- `OLLAMA_HOST`

Retorna dataclass `OdisseuConfig` com todos os campos tipados.
Falha explicitamente se `PROMETHEUS_ENGINE` não está definido.

### prometheus/client.py

`PrometheusClient` abstrai toda comunicação com o Prometheus.

```python
async def ask(query: str, ctx: str | None = None) -> str
async def search(query: str, ctx: str | None = None, top_k: int = 5) -> list[dict]
async def get_session_memory(project: str) -> str
async def save_adr(project: str, title: str, context: str,
                   decision: str, rationale: str) -> None
async def capture_til(text: str, tags: list[str]) -> None
```

**Modo `cli`** (padrão): usa `asyncio.subprocess` para chamar `pb ask`,
`pb search`, `pb til`, `pb adr add`. Timeout configurável por chamada (default 30s).

**Modo `mcp`** (avançado): conecta ao servidor MCP do Prometheus via stdio,
chama tools diretamente sem subprocess overhead.

### router/model_router.py

```python
class TaskComplexity(Enum):
    LOCAL = "local"               # ollama primary
    STANDARD = "standard"         # ollama com fallback API
    ARCHITECTURE = "architecture" # claude-opus direto
```

Regras de classificação:
- `LOCAL`: execução de shell, testes, operações de arquivo
- `STANDARD`: geração de código, análise, planejamento
- `ARCHITECTURE`: ADR, decisões de design, refatoração estrutural

Regras de fallback:
1. Tenta modelo local
2. Se timeout ou output malformado: retry local
3. Se falhar: usa API fallback (se `ANTHROPIC_API_KEY` disponível)
4. Se API indisponível: erro com sugestão `od retry --model api`

Rastreia custo por sessão. Bloqueia chamadas API se `daily_budget` esgotado.

### agent/planner.py

1. Busca contexto via `PrometheusClient.ask(goal)`
2. Busca ADRs via `PrometheusClient.get_session_memory()`
3. Monta prompt com contexto comprimido
4. Chama `ModelRouter` (STANDARD ou ARCHITECTURE conforme goal)
5. Parseia resposta JSON → lista de `Task` dataclasses
6. Valida: cada task tem `objective`, `acceptance_criteria` (≥1), `tests` (≥1)
7. Ordena por dependências (topological sort)
8. Retorna `Plan`

JSON esperado do modelo:

```json
{
  "goal": "...",
  "assumptions": ["..."],
  "tasks": [
    {
      "id": "t1",
      "objective": "...",
      "files": ["src/foo.py"],
      "acceptance_criteria": ["função X retorna Y para input Z"],
      "tests": ["pytest tests/test_foo.py::test_x -q"],
      "risk": "baixo",
      "rollback": "git checkout src/foo.py",
      "dependencies": []
    }
  ]
}
```

Se JSON malformado: retry uma vez com prompt de correção. Se falhar: aborta.

### agent/executor.py

Ferramentas disponíveis:

```python
async def run_shell(cmd: str, cwd: str, timeout: int) -> tuple[int, str, str]
async def write_file(path: str, content: str) -> None
async def read_file(path: str) -> str
async def run_tests(test_cmds: list[str], cwd: str) -> list[tuple[bool, str]]
async def git_diff() -> str
async def list_changed_files() -> list[str]
```

Fluxo:
1. Gera prompt de execução com task + contexto Prometheus comprimido
2. Chama `ModelRouter` para obter código/comandos
3. Parseia resposta: extrai blocos de código e comandos shell
4. Executa via `run_shell` com timeout configurado
5. Retorna `ExecutionResult` com output e `files_changed`

Segurança: nunca executa `rm -rf`, `git push --force`, `DROP TABLE` sem
confirmação explícita. Lista de comandos bloqueados configurável em `agent.toml`.
Toda execução shell logada com timestamp.

### agent/verifier.py

1. Roda comandos de teste da task (`task.tests`) via `run_shell`
2. Para cada `acceptance_criterion`: avalia se output confirma critério
3. Se testes passam: `criteria_met = True` para cada um coberto
4. Se critério não coberto por teste: usa LLM (LOCAL) para avaliar
5. Retorna `list[bool]` — uma entrada por criterion

Resultado:
- Todos `True` → task aprovada, avança
- Algum `False` → falhou, Orchestrator decide retry ou abort
- Timeout → falhou, registrado como timeout

### agent/learner.py

Após goal completo:

1. Se `goal.status == done` e `confidence >= threshold`:
   - Gera TIL compacto descrevendo o aprendizado (caveman-style)
   - Chama `PrometheusClient.capture_til(text, tags)`
2. Se task criou/modificou arquivo de arquitetura:
   - Gera ADR
   - Chama `PrometheusClient.save_adr(...)`
3. Grava summary no `AgentStore`: `tasks_count`, `success_rate`,
   `total_retries`, `duration_s`

### agent/orchestrator.py

```python
class GoalOrchestrator:
    async def run(self, goal_description: str, ctx: str | None) -> GoalResult:
        goal = await self._create_goal(goal_description, ctx)
        plan = await self.planner.plan(goal)
        await self.store.save_plan(plan)

        for task in plan.tasks:
            for attempt in range(self.config.max_retries):
                result = await self.executor.execute(task)
                verdict = await self.verifier.verify(task, result)
                if verdict.passed:
                    task.status = TaskStatus.DONE
                    break
                task.retries += 1
                if attempt == self.config.max_retries - 1:
                    task.status = TaskStatus.FAILED
                    if task.risk == "high":
                        await self._rollback(task)
                        goal.status = GoalStatus.ABORTED
                        return GoalResult(goal, plan, aborted=True)

        if all(t.status == TaskStatus.DONE for t in plan.tasks):
            goal.status = GoalStatus.DONE
            await self.learner.capture(goal, plan)
            if self.config.require_ship_approval:
                await self._prompt_ship_approval(plan)
        else:
            goal.status = GoalStatus.FAILED

        return GoalResult(goal, plan)
```

### store/agent_store.py

SQLite via aiosqlite. Schema:

```sql
CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    ctx TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals(id),
    objective TEXT NOT NULL,
    files TEXT,
    acceptance_criteria TEXT,
    tests TEXT,
    risk TEXT,
    rollback TEXT,
    dependencies TEXT,
    status TEXT NOT NULL,
    retries INTEGER DEFAULT 0,
    outcome TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE outcomes (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    tasks_count INTEGER,
    success_rate REAL,
    total_retries INTEGER,
    duration_s REAL,
    til_captured INTEGER DEFAULT 0,
    adr_saved INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
```

Métodos: `save_goal`, `get_goal`, `update_goal_status`, `save_task`,
`update_task_status`, `get_tasks_for_goal`, `save_outcome`, `get_recent_outcomes`.

Inicialização explícita via `.init()` — mesmo padrão do Prometheus `SessionStore`.

### cli/od.py

```
od run <goal>             executa goal completo
  --ctx                   prometheus context
  --no-approval           pula confirmação de ship
  --dry-run               só gera plano, não executa

od plan <goal>            só gera plano (mostra JSON)
  --ctx

od status                 lista goals recentes e status

od retry <goal-id>        retenta goal falho do ponto de falha
  --from-task <task-id>   recomeça de task específica

od history                outcomes anteriores com métricas
  --limit 10

od til                    lista TILs capturados automaticamente
```

Output: caveman mode por padrão. `od run` mostra progresso em tempo real:
task atual, attempt, resultado.

---

## pyproject.toml

```toml
[project]
name = "odisseu"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "litellm>=1.40",
    "aiosqlite>=0.20",
    "httpx>=0.27",
    "mcp>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff"]

[project.scripts]
od = "odisseu.cli.od:main"
```

---

## CLAUDE.md do Odisseu

Incluir:

1. Caveman output mode ativo por padrão (mesmo padrão do Prometheus)
2. Karpathy 4 principles: Think Before Coding, Simplicity First,
   Surgical Changes, Goal-Driven Execution
3. Nunca modificar Prometheus — só consumir via `PrometheusClient`
4. `AgentStore.init()` obrigatório antes de qualquer operação
5. Toda execução shell via `Executor` — nunca `subprocess` direto no código
6. Tests first: cada módulo novo tem teste antes da implementação

---

## docs/ADR.md inicial

| ADR | Decisão |
|---|---|
| ADR-001 | Odisseu consome Prometheus via PrometheusClient — nunca modifica |
| ADR-002 | SQLite como state store do agente (mesmo padrão Prometheus) |
| ADR-003 | Local-first model routing — API apenas como fallback explícito |
| ADR-004 | Ship requer aprovação humana por padrão (`require_ship_approval=true`) |
| ADR-005 | Learner só grava TIL/ADR se verifier confirma sucesso (confidence gate) |

---

## Critérios de aceite

1. `od run "add type hints to src/foo.py"` executa end-to-end sem erro
2. `od plan "refactor auth module"` retorna JSON válido com tasks e critérios
3. `od status` mostra goals da sessão com status correto
4. `PrometheusClient.ask()` chama `pb ask` e retorna string não vazia
5. `Verifier` retorna `False` quando teste falha, `True` quando passa
6. `Learner` não grava TIL se `success_rate < confidence_threshold`
7. `ModelRouter` nunca chama API se `ANTHROPIC_API_KEY` não definido
8. `AgentStore` persiste goal entre reinicializações do processo
9. `pytest tests/ -q` passa com cobertura de planner, executor, verifier, store
10. `ruff check src/` retorna zero erros

---

## Fora do escopo (v0)

- Interface web ou dashboard
- Tasks paralelas — sequencial nesta versão
- Integração com GitHub (PRs, issues)
- Streaming de output dos modelos
- Plugin system
- Autenticação ou multi-user

Foco: loop funcional e testável. Nenhuma feature além dos critérios de aceite.
