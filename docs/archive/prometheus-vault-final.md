# Prometheus: Documentos Finais do Vault

---

## CLAUDE.md Global (~/vault/CLAUDE.md)

```markdown
# Prometheus Vault

Você está no segundo cérebro do Sammy Junior.
Senior Full Stack Engineer (Java + React), João Pessoa, Brasil.
Trabalhando remotamente com empresas US-based.

## Quem sou

- **Trabalho atual:** Avangrid (Java/Spring Boot, EKS, Kafka)
- **Projetos pessoais:** Aerus RPG, rpg-master-ai, LinkedIn tool
- **Stack principal:** Java 21, Spring Boot, React/TypeScript, Kafka, AWS
- **Buscando:** Senior L3+ US remote, pagamento em USD

## Métricas reais (use em respostas de entrevista e posts)

- EKS observability: detecção de incidentes de ~1h para <10min (Avangrid)
- PingOne OAuth2/OIDC: cobertura de testes de 40% para 90% (Yubico)
- Migração de 10M+ registros com zero downtime (TCU)
- rpg-master-ai: 13.7x speedup em batch embedding, gRPC vs HTTP documentado

## Contextos disponíveis

| Pasta      | Contexto  | Acesso padrão | Requer confirmação |
|------------|-----------|---------------|--------------------|
| personal/  | Projetos  | Livre         | Não                |
| career/    | Carreira  | Livre         | Não                |
| knowledge/ | Técnico   | Livre         | Não                |
| work/      | Avangrid  | BLOQUEADO     | Sempre             |

Nunca acesse work/ sem confirmação explícita do usuário.

## Regras de navegação

1. Leia o daily note de hoje se existir: daily/YYYY-MM-DD.md
2. Leia o CONTEXT.md da pasta do projeto ativo antes de trabalhar
3. Ao terminar, escreva resumo no daily note (o que foi feito, decisões, próximos passos)
4. ADRs arquiteturais vão em <projeto>/adrs/YYYY-MM-DD-titulo.md
5. Nunca escreva código proprietário do Avangrid no vault

## Preferências de código

- Java: records > classes anêmicas, virtual threads para I/O, sem Lombok
- Python: type hints sempre, dataclasses > dicts, async por padrão
- Sem comentários óbvios, código deve ser autodocumentado
- Testes: Testcontainers para integração, sem mocks de repositório

## Formato de ADR

# ADR-XXX: Título
**Data:** YYYY-MM-DD | **Projeto:** nome | **Status:** Aceito

### Contexto
Por que essa decisão precisou ser tomada.

### Decisão
O que foi decidido.

### Justificativa
Por que essa opção e não as alternativas.

### Consequências
O que muda. O que fica mais difícil.
```

---

## CONTEXT.md por Projeto (template base)

Crie um desses em cada pasta de projeto. Preencha com o que o Claude precisa saber antes de começar a trabalhar.

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
Abordagens que já foram tentadas e descartadas, com motivo.

## Como rodar localmente
```bash
# comandos mínimos para subir o projeto
```

## Contexto para entrevistas
Como descrevo esse projeto em 2-3 frases com métricas.
```

---

### CONTEXT.md preenchido: Aerus RPG (exemplo)

```markdown
# Aerus RPG

## O que é
Multiplayer text RPG com mundo dark fantasy isekai.
Python/FastAPI backend, React/TypeScript frontend,
LLM routing via OpenRouter para narração adaptativa.

## Stack
- Backend: Python 3.12, FastAPI, SQLite, ChromaDB
- Frontend: React 18, TypeScript, Vite
- LLMs: Gemini Flash (MVP), Claude Sonnet (narração),
         Claude Opus (momentos críticos), Ollama (mecânicas)
- Infra: self-hosted, Docker Compose

## Estado atual
- World building completo (5 continentes, 4 facções, 17 sub-raças)
- 8 classes com árvores de mutação/ascensão documentadas
- Antagonista central: Vor'Athek
- Backend: estrutura básica, sistema de combate em progresso
- Frontend: não iniciado

## Próximos passos
1. Finalizar sistema de combate (backend)
2. Implementar session memory para continuidade narrativa
3. Scaffold do frontend React

## Decisões importantes
- ADR-001: Gemini Flash para MVP por custo, não por qualidade
- ADR-002: ChromaDB sobre Qdrant por simplicidade no MVP
- ADR-003: SQLite sobre PostgreSQL até precisar de escala

## O que NÃO fazer
- Não implementar multiplayer real-time ainda (WebSocket vem depois)
- Não usar um único modelo LLM para tudo (roteamento é o diferencial)

## Como rodar
```bash
cd ~/projects/aerus-rpg
docker compose up -d
uvicorn src.main:app --reload
```

## Para entrevistas
"Sistema de RPG multiplayer com pipeline multi-agent de LLMs.
Roteamento inteligente entre Gemini, Claude e modelos locais
baseado no tipo de task: narração, mecânicas ou momentos críticos."
```

---

## Templates de Knowledge

### Template D: TIL (captura rápida)

```markdown
---
tags: [TECNOLOGIA, SUBTAG]
created: YYYY-MM-DD
type: til
promoted: false
---

# TIL: TÍTULO EM UMA LINHA

DESCRIÇÃO EM 2-3 FRASES. O que é, por que importa.

```LINGUAGEM
// código mínimo se aplicável
```

Contexto: onde/como descobri isso.
```

**Criar via CLI:**
```bash
pb til "records java não aceitam herança"
# Cria knowledge/java/til-2026-04-19-records-heranca.md automaticamente
```

---

### Template E: HOW-TO (receita reproduzível)

```markdown
---
tags: [TECNOLOGIA, SUBTAG]
created: YYYY-MM-DD
type: howto
verified: true
promoted_from: til-YYYY-MM-DD  # se veio de um TIL
---

# HOW-TO: TÍTULO ACIONÁVEL

## Quando usar
Contexto específico onde essa solução se aplica.
Quando NÃO usar (igualmente importante).

## Dependências
```xml ou bash
# o que precisa estar instalado/configurado
```

## Código mínimo
```LINGUAGEM
// implementação mínima que funciona
```

## Armadilha principal
O erro mais comum e como evitar.

## Usado em
- [[projeto/arquivo]] — contexto de uso real
```

---

## Script de conversão automática TIL → HOW-TO

Roda via git hook no fim de cada sessão. Analisa os TILs do dia e promove os que têm substância suficiente.

```python
# src/vault/til_promoter.py

import re
from pathlib import Path
from datetime import date
import litellm

VAULT_PATH = Path.home() / "vault"
KNOWLEDGE_PATH = VAULT_PATH / "knowledge"
TODAY = date.today().isoformat()

PROMOTION_CRITERIA = """
Analise este TIL e decida se deve ser promovido para HOW-TO.

Promova se tiver DOIS ou mais:
- Trecho de código concreto
- Uma armadilha ou erro comum identificado
- Contexto de uso real (onde foi aplicado)
- Algo reproduzível (não só uma observação)

Responda apenas: PROMOTE ou KEEP
"""

def find_todays_tils() -> list[Path]:
    return [
        f for f in KNOWLEDGE_PATH.rglob("til-*.md")
        if TODAY in f.name and not _is_promoted(f)
    ]

def _is_promoted(path: Path) -> bool:
    content = path.read_text()
    return "promoted: true" in content

def should_promote(til_content: str) -> bool:
    response = litellm.completion(
        model="ollama/gemma4:e4b",   # local, grátis
        messages=[
            {"role": "system", "content": PROMOTION_CRITERIA},
            {"role": "user", "content": til_content},
        ],
        max_tokens=10,
    )
    return "PROMOTE" in response.choices[0].message.content

def promote_to_howto(til_path: Path) -> Path:
    til_content = til_path.read_text()

    prompt = f"""
Converta este TIL em um HOW-TO seguindo exatamente este formato:

---
tags: [extraia as tags do TIL]
created: {TODAY}
type: howto
verified: true
promoted_from: {til_path.name}
---

# HOW-TO: [título acionável]

## Quando usar
[contexto de uso]

## Código mínimo
[código do TIL formatado]

## Armadilha principal
[problema identificado]

## Usado em
[contexto do TIL]

TIL original:
{til_content}
"""

    response = litellm.completion(
        model="ollama/gemma4:e4b",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
    )

    howto_content = response.choices[0].message.content
    howto_path = til_path.parent / til_path.name.replace("til-", "howto-")
    howto_path.write_text(howto_content)

    # Marca o TIL como promovido
    updated = til_content.replace("promoted: false", "promoted: true")
    til_path.write_text(updated)

    return howto_path

def run():
    tils = find_todays_tils()
    if not tils:
        return

    print(f"Analisando {len(tils)} TIL(s) do dia...")
    promoted = 0

    for til_path in tils:
        content = til_path.read_text()
        if should_promote(content):
            howto_path = promote_to_howto(til_path)
            print(f"  Promovido: {til_path.name} → {howto_path.name}")
            promoted += 1
        else:
            print(f"  Mantido como TIL: {til_path.name}")

    if promoted:
        print(f"\n{promoted} HOW-TO(s) criados. Revise quando quiser.")

if __name__ == "__main__":
    run()
```

---

## Git hook completo com promoção automática

```bash
#!/bin/bash
# ~/vault/.git/hooks/post-commit

echo "Sincronizando vault..."

# Promoção de TILs (roda local antes de subir)
python3 ~/prometheus/src/vault/til_promoter.py

# Se o promoter criou novos HOW-TOs, adiciona ao commit
NEW_FILES=$(git status --porcelain | grep "howto-" | wc -l)
if [ "$NEW_FILES" -gt 0 ]; then
    git add knowledge/
    git commit -m "auto: promote TILs to HOW-TOs [skip ci]" --quiet
fi

# Sync com GitHub
git pull --rebase origin main --quiet
git push origin main --quiet

echo "Vault sincronizado."
```

---

## pb CLI: comandos de knowledge

```bash
# Captura rápida (não sai do flow)
pb til "virtual threads são ruins para CPU-bound"
pb til "Spring @Transactional ignora chamada interna" --tags spring,aop

# Criar HOW-TO manualmente quando vale a pena
pb howto --from til-2026-04-19-transactional.md

# Buscar no knowledge vault
pb search "testcontainers postgresql" --ctx=knowledge
pb search "kafka partition key" --ctx=knowledge

# Ver TILs pendentes de promoção
pb til --list --pending

# Forçar promoção de todos os TILs do dia
pb til --promote-today
```

---

## Estrutura final do vault

```
~/vault/
├── CLAUDE.md                          # global, progressivo
├── .gitignore                         # .obsidian/workspace, .DS_Store
│
├── personal/
│   ├── .ctx
│   ├── aerus-rpg/
│   │   ├── CONTEXT.md                 # preenchido acima
│   │   ├── adrs/
│   │   ├── world/
│   │   └── backlog.md
│   ├── rpg-master-ai/
│   │   └── CONTEXT.md
│   └── linkedin-tool/
│       └── CONTEXT.md
│
├── career/
│   ├── .ctx
│   ├── CONTEXT.md
│   ├── metrics.md                     # suas métricas verificadas
│   ├── interviews/
│   ├── targets/
│   └── linkedin/
│
├── knowledge/
│   ├── .ctx
│   ├── java/
│   │   ├── til-2026-04-19-records-heranca.md
│   │   └── howto-2026-04-19-virtual-threads.md
│   ├── spring/
│   ├── kafka/
│   ├── ai-engineering/
│   └── system-design/
│
├── work/
│   ├── .ctx
│   ├── .ctxguard                      # barreira explícita
│   └── avangrid/
│       └── CONTEXT.md
│
└── daily/
    └── 2026-04-19.md
```

---

## .gitignore do vault

```
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.DS_Store
*.tmp
```

O restante do `.obsidian/` (plugins, themes, config) pode ser versionado: garante que o Obsidian abre igual nos dois dispositivos.
