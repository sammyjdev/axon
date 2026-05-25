# dec-106 — Routing profiles: FREE (Groq + NIM) e PAID (OpenRouter + Groq pago)

- Status: accepted
- Date: 2026-05-25

## Context

O router de AXON (D2 / ADR-002) define tiers Haiku/Sonnet/Opus rotando direto
pra API Anthropic. Dois cenários reais não são bem servidos por isso:

1. **Hardware modesto sem orçamento de API.** Mac 16GB não roda Ollama em
   modelos úteis (gemma4:26b sai de questão; phi3:mini cai em qualidade).
   Sem API key, AXON fica inoperante.
2. **Quem tem créditos pagos preferiria OpenRouter** (unifica billing, sem
   precisar gerenciar key Anthropic separada) mas mantendo o mesmo perfil
   de tier Claude que D2 define.

dec-102 congelou expansão do router. Esta decisão introduz o conceito de
**profile** como configuração — sem novos modes, sem novos profiles TOML —
respeitando esse congelamento.

## Decision

Introduzir dois routing profiles selecionáveis via `AXON_PROVIDER_PROFILE`:

### FREE (default)

Sem custo direto, sujeito a rate limits dos provedores.

| TaskType | Modelo |
| --- | --- |
| TRIVIAL_COMPLETION | `groq/llama-3.1-8b-instant` |
| CODE_ANALYSIS | `groq/llama-3.3-70b-versatile` |
| ARCHITECTURE | `nvidia_nim/meta/llama-3.1-70b-instruct` |
| DEEP_REASONING | `nvidia_nim/meta/llama-3.1-70b-instruct` |
| LOCAL_ONLY | `groq/llama-3.1-8b-instant` |
| UNKNOWN | `groq/llama-3.1-8b-instant` |
| classifier | `groq/llama-3.1-8b-instant` |

### PAID

Crédito pago, preserva D2 via OpenRouter.

| TaskType | Modelo |
| --- | --- |
| TRIVIAL_COMPLETION | `openrouter/anthropic/claude-haiku-4` |
| CODE_ANALYSIS | `openrouter/anthropic/claude-sonnet-4` |
| ARCHITECTURE | `openrouter/anthropic/claude-opus-4` |
| DEEP_REASONING | `openrouter/anthropic/claude-opus-4` |
| LOCAL_ONLY | `openrouter/anthropic/claude-haiku-4` |
| UNKNOWN | `openrouter/anthropic/claude-haiku-4` |
| classifier | `groq/llama-3.1-8b-instant` (cheap) |

### Mudanças no engine

- Mapping `task_type → model` agora vem de `axon.router.profiles`, não mais
  hard-coded em `engine.py`.
- Downgrade por budget vira **task-type-driven** (não compara strings de
  modelo): ARCHITECTURE/DEEP_REASONING cai pra CODE_ANALYSIS quando estoura
  `_OPUS_BUDGET`; CODE_ANALYSIS cai pra TRIVIAL_COMPLETION quando estoura
  `_BUDGET_USD`. Funciona pra qualquer profile.
- `request_opus=True` continua bypassando o gate de Opus budget.
- Classifier deixa de usar SDK do Ollama diretamente; vai sempre via LiteLLM
  com o modelo do profile.
- Validação de compliance OpenRouter (`validate_openrouter_compliance`)
  vira opt-in via `AXON_OPENROUTER_COMPLIANCE=1` (default off). Isso evita
  quebrar PAID profile pra uso pessoal onde compliance metadata não faz
  sentido. Quem precisa do guardrail original re-habilita explicitamente.
- `AXON_PROVIDER_OLLAMA` muda default de `1` pra `0`. Ollama continua
  suportado (ADR-003/D3 intacto), mas habilita explicitamente.

## Rationale

- **Profile FREE resolve o caso "Mac sem hardware + sem budget de API"**
  com qualidade decente (Llama 70B free na NIM é muito melhor que phi3:mini
  local).
- **Profile PAID preserva D2** semanticamente — só troca o transport
  (Anthropic native → OpenRouter). Não viola ADR-002.
- **Sem expandir surface do router** (dec-102): profiles são config, não
  feature nova. Não há `axon profile create`, não há novos modes.
- **Downgrade task-type-driven** é mais limpo que comparação de strings,
  e escala pra qualquer profile futuro sem refatoração.

## Consequences

- **ARD-001 (context isolation) reforçado por incompatibilidade**: profiles
  FREE/PAID são puramente cloud. Para `ctx=work` (restricted), policy
  bloqueia cloud — então essas tasks falham. **Suporte a work ctx fica
  fora de escopo deste roadmap** (ver Out-of-scope abaixo). Os guardrails
  em `policy/core.py` e a marca `.ctxguard` continuam ativos: work ctx
  segue protegido contra cloud, só não há caminho de execução.
- **Rate limit gate implementado** em `axon/resilience/rate_limiter.py`
  (fixed-window por minuto e por dia, Redis com memory fallback).
  Configurável via `AXON_<PROVIDER>_MAX_RPM` / `AXON_<PROVIDER>_MAX_RPD`.
  Defaults: Groq 25/min e 13000/dia (margem sob 30/14400 reais), NIM
  50/min e 950/dia. Quando estoura, `complete()` e classifier levantam
  `RuntimeError(DENY_RATE_LIMIT)` — não conta como falha de modelo (não
  abre o circuit breaker).
- **Custo do PAID profile não bate exatamente com D2 nativa.** OpenRouter
  adiciona ~10% sobre Anthropic direto. `_COST_PER_1K` em `profiles.py`
  reflete estimativa OpenRouter, não preço Anthropic nativo.
- **Slugs OpenRouter** (`openrouter/anthropic/claude-{haiku,sonnet,opus}-4`)
  podem mudar quando OpenRouter versionar. Override via
  `AXON_PROVIDER_PROFILE` + edição local de `profiles.py` ou env var futura.
- **`expansion/service.py` continua usando `_RUNTIME.classifier_cloud_model`**
  — agora resolve pelo profile, então custo estimado pode ficar 0 (Groq
  free). Aceitável.

## Out-of-scope

- **Suporte a `ctx=work` neste roadmap.** Hardware-alvo (Mac 16GB) não
  comporta Ollama em modelo útil, e cloud é vedada por ARD-001. Habilitar
  work ctx exigiria infra remota dedicada (Ollama self-hosted em outra
  máquina via `AXON_OLLAMA_REMOTE_HOST`), o que está fora do foco atual.
  Guardrails ficam ativos — tasks com `ctx=work` continuam sendo bloqueadas
  pela policy, comportamento correto.
- **Suporte multi-profile dinâmico** (trocar de profile mid-session). Profile
  é lido na carga do módulo; restart resolve.

## Migration

Para usuários existentes:

1. Sem ação: default vira FREE, requer `GROQ_API_KEY` e `NVIDIA_NIM_API_KEY`.
2. Se quiser manter D2 nativa Anthropic: setar `AXON_PROVIDER_PROFILE=paid`
   **não** funciona pra isso (PAID usa OpenRouter). Manter export de
   `ANTHROPIC_API_KEY` e editar `profiles.py` localmente pra apontar pra
   slugs nativos (`claude-haiku-4-5-20251001` etc.).
3. Se tinha Ollama configurado: setar `AXON_PROVIDER_OLLAMA=1` explícito
   (default mudou pra 0) e usar `AXON_CLASSIFIER_CLOUD_MODEL` apontando
   pra `ollama/<modelo>` (LiteLLM resolve).
