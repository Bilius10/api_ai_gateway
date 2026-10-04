# Stateless AI Gateway

Gateway local para uniformizar geração de texto entre Codex CLI, OpenAI, Gemini, Ollama, Anthropic, OpenRouter e endpoints OpenAI-compatible. O painel Angular expõe Dashboard, Providers, Routing, Requests, Playground e Settings.

> **Segurança:** o primeiro corte não possui autenticação. Vincule o backend a `127.0.0.1` ou use somente uma rede confiável. Nunca o exponha diretamente à internet.

## Executar no Windows

Na raiz do projeto, dê duplo clique em:

```text
api-ai-gateway.exe
```

Ele abre dois terminais:

- Backend/API: `http://127.0.0.1:8000` e `http://<ip-da-maquina>:8000`
- App desktop local com a interface administrativa

O frontend continua rodando localmente para alimentar a janela do app, mas o uso normal é pelo app aberto pelo executável, não por uma aba do navegador.

O executável não exige DevX nem WSL. Ele usa PowerShell, Python e Node instalados no Windows. Quando `uv` estiver disponível, usa `uv`; caso contrário, cria `backend/.venv` com Python e instala as dependências via `pip`.

## Pré-requisitos sem DevX

Para executar em outra máquina sem DevX:

- Windows 10/11 para usar `api-ai-gateway.exe`.
- PowerShell.
- Python `3.12+` no Windows.
- Node.js `22+` com `npm` no Windows.
- `uv` é opcional, mas recomendado.
- Codex CLI é opcional e só é necessário se você habilitar o provider Codex CLI.

Para Linux/macOS, use a execução manual abaixo.

## Executar Manualmente Em Linux/macOS

Backend:

```bash
cd /home/joao_oliveira/workspaces/api-ai-gateway
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
mkdir -p ../.cache
if [ ! -f ../.cache/ai-gateway-master-key.env ]; then
  printf 'AI_GATEWAY_MASTER_KEY=%s\n' "$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" > ../.cache/ai-gateway-master-key.env
fi
. ../.cache/ai-gateway-master-key.env
export AI_GATEWAY_MASTER_KEY
python -m uvicorn ai_gateway.app:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```bash
cd /home/joao_oliveira/workspaces/api-ai-gateway
cd frontend
npm ci
npm start -- --host 127.0.0.1 --port 4200
```

## DevX

DevX é opcional. Ele pode ser usado para gerar um ambiente sandbox/reprodutível com as versões registradas em `devx.lock`, mas o código do backend, o frontend e o executável de execução não dependem dele.

Quando quiser usar DevX:

```powershell
devx workspace bootstrap --path /home/joao_oliveira/workspaces/api-ai-gateway
```

Depois, execute normalmente pelo `api-ai-gateway.exe` ou pelos comandos manuais acima.

## Recriar o Executável

O fonte do launcher fica em `launcher/AiGatewayLauncher.cs`. Para recriar `api-ai-gateway.exe` no Windows:

```powershell
powershell.exe -NoProfile -Command "Add-Type -Path .\launcher\AiGatewayLauncher.cs -OutputAssembly .\api-ai-gateway.exe -OutputType ConsoleApplication"
```

Para validar sem abrir os servidores:

```powershell
.\api-ai-gateway.exe --check
```

O executável gerado usa o runtime .NET já presente no Windows 10/11.

## Configuração

Variáveis do backend:

- `AI_GATEWAY_DATABASE_PATH`: banco SQLite local; padrão `.data/gateway.db`.
- `AI_GATEWAY_CREDENTIAL_FILE`: arquivo criptografado; padrão `.data/secrets.enc`.
- `AI_GATEWAY_MASTER_KEY`: chave Fernet URL-safe de 32 bytes. O executável gera uma chave local em `.cache/ai-gateway-master-key.env` quando ela não existir.
- `AI_GATEWAY_CODEX_EXECUTABLE`: executável do Codex CLI; padrão `codex`.
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` e `DASHSCOPE_API_KEY`: credenciais opcionais lidas diretamente do ambiente.

Providers customizados podem ser criados pela API `POST /api/providers`. Credenciais enviadas no campo `api_key` só são aceitas quando `AI_GATEWAY_MASTER_KEY` estiver presente e são armazenadas criptografadas. Prompts e respostas nunca são persistidos; somente IDs, provider/modelo, latência, status, erro sanitizado e tentativas.

## Verificação

Backend:

```bash
cd backend
python -m pytest
python -m ruff check .
python -m mypy src
```

Com `uv`:

```bash
cd backend
uv run pytest
uv run ruff check .
uv run mypy src
```

Frontend:

```bash
cd frontend
npm test -- --watch=false
npm run build
```

Smoke test:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/providers
curl -N -H 'content-type: application/json' -d '{"prompt":"hello","stream":true}' http://127.0.0.1:8000/api/generate/stream
```

Sem provider habilitado, geração responde `503` estruturado. No modo manual não há fallback. No modo automático, timeout, rate limit, quota, indisponibilidade e 5xx recuperáveis avançam para o próximo provider por prioridade. Streaming nativo é usado nos providers OpenAI-compatible (OpenAI, OpenRouter, DeepSeek, Qwen e genéricos). Codex, Gemini, Ollama e Anthropic emitem a resposta completa como um único evento SSE neste primeiro corte. O fallback de streaming só ocorre antes de qualquer conteúdo ser entregue; depois do primeiro trecho, um erro encerra o stream para não misturar respostas de providers diferentes.
