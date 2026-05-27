# dec-112 — SQLite WAL + pending dir + drain idempotente; sem daemon

- Status: accepted
- Date: 2026-05-27

## Context

`SessionStore` (`src/axon/store/session_store.py`) hoje usa `aiosqlite`
com `asyncio.Lock()` in-process e **sem `PRAGMA journal_mode=WAL`**
(rollback journal default). Cada CLI invocation abre conexão própria.
Multi-processo concorrente (dois hooks paralelos, agente + dev CLI
simultâneos) entra em contenção real de lock.

Red-team R1 identificou risco de `database is locked` sob carga
multi-agente. R2 propôs daemon + socket Unix; rejeitado por quebrar
Windows nativo e introduzir IPC complexa. R3 identificou que retry
sem fallback definido causa drift de estado. R4 identificou corrida
de escrita/drenagem no fallback file. R5 identificou ausência de
isolamento de erro no drain.

## Decision

### Concorrência SQLite

`SessionStore._connection()` aplica no connect:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```

Multi-writer cross-process via SQLite nativo. WAL permite readers
concorrentes durante writes.

### Retry com fallback

Sob `SQLITE_BUSY`, writes seguem:

```
retry com backoff exponencial + jitter
budget total: 2 * busy_timeout = 10s
após esgotar:
  1. escreve a captura em .axon/pending/{commit_hash}-{ts_ns}.json
     (path único por construção; rename atômico)
  2. emite warning estruturado em .axon/capture-warnings.jsonl
  3. retorna sucesso ao hook (NUNCA quebra git)
```

Garantia dura: **hook nunca quebra git por causa de captura.**

### Pending dir, não fallback file único

`.axon/context.md` deixa de ser sink ativo. Vira **view derivada**,
regenerada do `pending/` consumido + estado do SessionStore.

Vantagens de pending dir sobre arquivo único:

- Paths únicos (`commit_hash + ts_ns`) eliminam colisão por construção
- `rename` em filesystem POSIX é atômico
- Sem necessidade de `flock` ou append-only com PIPE_BUF
- Drainer enumera, processa em ordem cronológica via stat, deleta
- Crash mid-drain: arquivo permanece em `pending/`, próximo drain
  processa
- Idempotência natural: chave `(commit_hash, ts_ns)` única; reprocessar
  é seguro

### Drain

Disparado por:

- Próximo `pb capture-*` bem-sucedido
- Hook `post-merge` / `post-checkout`
- `pb doctor` (informativo)
- `pb drain` manual

Loop de drain:

```
para cada arquivo em .axon/pending/ (ordem cronológica):
  try:
    parse JSON
    write para SessionStore (com retry SQLite)
    delete arquivo
  except (JSONDecodeError, UnicodeError, ValueError, ...):
    move para .axon/pending-quarantine/{basename}.{ts}.json
    append em .axon/quarantine.jsonl: {original_path, reason,
                                       exception, ts}
    continue  # não trava o loop
  except SQLITE_BUSY após retry esgotado:
    deixa em pending/, próximo drain tenta novamente
```

Quarantine **nunca** é apagada automaticamente — preserva evidência
para debug. `pb pending recover [--id=X]` permite re-tentativa manual.

### Não implementar

- Daemon process
- Socket Unix / Named Pipes / HTTP loopback
- Fila externa
- Postgres opcional
- `flock` ou outras primitivas de coordenação

## Rationale

- **Load real é baixo**: hooks de dev local geram ~1 write/s
  sustentado; SQLite WAL aguenta ~100 writes/s sem contenção.
- **Pending dir + paths únicos** elimina corrida de escrita por
  construção, sem dependência de primitiva POSIX.
- **Daemon era overengineering**: quebrava Windows nativo, introduzia
  IPC complexa, e o load não justifica.
- **Fallback file (dec-103)** continua válido como view derivada
  para agentes sem MCP, agora alimentado pelo drain consumido.
- **Quarantine pattern** padrão de fila resiliente: payload corrompido
  não bloqueia processamento dos válidos.

## Consequences

- `SessionStore._connection()` aplica PRAGMAs no abre.
- Writes encapsulados em retry helper (`axon.store.retry`).
- Novo módulo `axon.store.pending` com `write()`, `drain()`,
  `quarantine_invalid()`.
- `.axon/pending/`, `.axon/pending-quarantine/`,
  `.axon/capture-warnings.jsonl`, `.axon/quarantine.jsonl` adicionados
  ao layout do repo.
- `.gitignore` deve incluir `.axon/pending/`,
  `.axon/pending-quarantine/`, `.axon/*.jsonl` (decisão do usuário se
  versionar drafts ou não — não bloqueante).
- `pb doctor` reporta backlog persistente em `pending/` e tamanho do
  `quarantine/` ([dec-114](dec-114-doctor-diagnostic-first.md)).
- Testes existentes do SessionStore podem precisar ajuste (mocks que
  assumiam rollback journal).
- Aceito como risco residual: filesystems sem rename atômico (alguns
  FUSE) não suportados para pending path — documentado em
  `SUPPORT_MATRIX.md`.
- Aceito como risco residual: pending dir pode acumular se
  SessionStore fica down indefinidamente — doctor reporta, self-heal
  no próximo drain.
