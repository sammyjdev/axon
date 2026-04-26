# Prometheus — Guia de Bootstrap do Vault

Guia passo a passo para configurar o `~/vault/` do Obsidian como segundo cérebro conectado ao Prometheus. Execute uma vez por máquina. O engine já precisa estar rodando (ver `README.md`).

---

## Pré-requisitos

Antes de começar, confirme que o engine está funcional:

```bash
# 1. Infra Docker rodando
docker compose ps | grep -E "qdrant|redis|ollama"

# 2. CLI instalado
pb --help

# 3. Modelos Ollama disponíveis
ollama list | grep -E "gemma4:e4b|phi3:mini"
```

Se a infra não estiver rodando, execute:

```bash
./setup.sh          # detecta plataforma Mac/PC, sobe Docker e puxa modelos
pipx install --editable /Users/samdev/dev/Prometheus
```

---

## 1. Criar estrutura de pastas do vault

```bash
VAULT=~/vault
mkdir -p \
  "$VAULT/knowledge/daily" \
  "$VAULT/knowledge/deep" \
  "$VAULT/career" \
  "$VAULT/personal" \
  "$VAULT/work" \
  "$VAULT/adrs"
```

---

## 2. Criar CLAUDE.md global do vault

O arquivo `~/vault/CLAUDE.md` é lido por Claude Code ao abrir qualquer projeto no vault. Cole e preencha conforme sua realidade:

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

| Pasta      | Contexto | Acesso padrão | Requer confirmação |
| ---------- | -------- | ------------- | ------------------ |
| personal/  | Projetos | Livre         | Não                |
| career/    | Carreira | Livre         | Não                |
| knowledge/ | Técnico  | Livre         | Não                |
| work/      | Avangrid | BLOQUEADO     | Sempre             |

Nunca acesse work/ sem confirmação explícita do usuário.

## Regras de navegação

1. Leia o daily note de hoje se existir: daily/YYYY-MM-DD.md
2. Leia o CONTEXT.md da pasta do projeto ativo antes de trabalhar
3. Ao terminar, escreva resumo no daily note (o que foi feito, decisões, próximos passos)
4. ADRs arquiteturais vão em <projeto>/adrs/YYYY-MM-DD-titulo.md
5. Nunca escreva código proprietário da Avangrid no vault

## Preferências de código

- Java: records > classes anêmicas, virtual threads para I/O, sem Lombok
- Python: type hints sempre, dataclasses > dicts, async por padrão
- Sem comentários óbvios, código deve ser autodocumentado
- Testes: Testcontainers para integração, sem mocks de repositório
```

---

## 3. Criar barreira de contexto work

```bash
# Marker que indica ao Prometheus que este diretório exige ctx=work explícito
touch ~/vault/work/.ctxguard
echo "context=work" > ~/vault/work/.ctxguard
```

O arquivo `.ctxguard` sinaliza à coleção de busca que qualquer acesso a este diretório requer `--ctx work` explícito. Sem ele, a barreira ainda funciona por coleção separada no Qdrant, mas o arquivo serve como documentação visual e como marker para scripts futuros.

---

## 4. Inicializar indexação por contexto

Execute uma vez para criar as coleções no Qdrant e indexar o conteúdo inicial:

```bash
# Criar coleções e indexar (pode ser em branco no início)
pb index ~/vault/knowledge --ctx knowledge
pb index ~/vault/career --ctx career
pb index ~/vault/personal --ctx personal

# work: só indexar com ctx explícito
# pb index ~/vault/work --ctx work
```

---

## 5. Instalar git hook de promoção automática

O hook faz promoção automática de TIL → HOW-TO ao fim de cada commit no vault:

```bash
bash /Users/samdev/dev/Prometheus/scripts/install_vault_hook.sh
chmod +x ~/.git/hooks/post-commit   # garante executável
```

Verifique:

```bash
test -x ~/.git/hooks/post-commit && echo "Hook OK" || echo "Hook ausente"
```

---

## 6. Testar o fluxo completo

```bash
# Registrar um TIL de teste
pb til "teste de setup do vault" --tags setup,prometheus

# Verificar se foi criado
ls ~/vault/knowledge/daily/$(date +%Y-%m-%d)/til-*.md

# Busca semântica
pb search "setup vault" --ctx knowledge --top 3

# Consulta com detecção de contexto
pb ask "como indexar o vault do prometheus"
```

---

## 7. Configurar watcher contínuo (macOS)

Para reindexação automática ao salvar no vault:

```bash
pb watch ~/vault/knowledge --ctx knowledge
```

Para rodar no boot via LaunchAgent, configure `~/Library/LaunchAgents/dev.samdev.colima.plist` conforme descrito em `README.md#macOS--boot-automático`.

---

## 8. Abrir o vault no Obsidian

1. Abrir Obsidian → "Open folder as vault" → selecionar `~/vault/`
2. Desabilitar Safe Mode se necessário (plugins como Dataview ou Templates)
3. O `CLAUDE.md` global já estará disponível para agentes

---

## Checklist de verificação

Execute após o setup para confirmar que tudo está funcional:

```bash
# Serviços
docker compose ps | grep -E "qdrant|redis|ollama" | grep -v Exit

# Coleções criadas no Qdrant
curl -s http://localhost:6333/collections | grep -E "knowledge|career|personal"

# CLI
pb --version

# Hook git
test -x ~/.git/hooks/post-commit && echo "OK" || echo "AUSENTE"

# Modelos Ollama
ollama list | grep -E "gemma4:e4b|phi3:mini"

# Busca funcional (retorna ao menos 0 resultados sem erro)
pb search "test" --ctx knowledge --top 1
```

---

## Troubleshooting

| Sintoma                                 | Causa mais provável           | Solução                                                          |
| --------------------------------------- | ----------------------------- | ---------------------------------------------------------------- |
| `pb search` retorna 0 resultados sempre | Coleções ainda não criadas    | Rode `pb index ~/vault/knowledge --ctx knowledge`                |
| `pb til --promote-today` silencioso     | Ollama não está rodando       | `docker compose up -d` e verificar `ollama list`                 |
| Watcher para após alguns minutos        | Processo encerrado            | Reiniciar `pb watch` ou verificar LaunchAgent                    |
| `RTK não instalado`                     | Binário `rtk` externo ausente | É opcional. Não necessário para operação padrão                  |
| `work` acessível sem `--ctx`            | Barreira não está configurada | Verificar `src/prometheus/store/collections.py`                  |
| Collections ausentes após restart       | Qdrant em volume efêmero      | Verificar `docker-compose.yml` — volumes devem ser mapeados      |
| TIL criado mas não aparece na busca     | Watcher não está rodando      | Rodar `pb index` manualmente ou reiniciar watcher                |
| `gemma4:26b` lento ou OOM no Mac 16GB   | VRAM insuficiente             | Usar `gemma4:e4b` como fallback via `OLLAMA_MAX_LOADED_MODELS=1` |
