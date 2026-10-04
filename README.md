# AI Gateway

Aplicativo desktop stateless para centralizar chamadas a Codex CLI, OpenAI, Gemini, Ollama, Anthropic, OpenRouter, DeepSeek, Qwen e endpoints OpenAI-compatible.

A interface administrativa é incorporada ao aplicativo Go + Wails. Não existe servidor Angular, porta `4200`, navegador externo ou CLI do produto. O aplicativo inicia um backend FastAPI nativo embutido e mantém a API disponível em:

```text
http://127.0.0.1:8000
http://<ip-da-maquina>:8000
```

> O primeiro corte não possui autenticação. Use somente no computador local ou em rede confiável. Não exponha a porta `8000` diretamente à internet.

## Instalar e executar

No Windows, baixe `AIGatewaySetup.exe` diretamente na raiz do repositório e execute-o. O instalador segue o mesmo padrão do DevX: instala em `%LOCALAPPDATA%\Programs\AI Gateway`, cria atalho no Menu Iniciar e registra a desinstalação.

- Windows: `AIGatewaySetup.exe`
- Linux: `ai-gateway_0.1.0_amd64.deb` (Ubuntu/Debian) ou o executável `api-ai-gateway`

O pacote final não exige Python, Node, `uv`, Go ou WSL. No Windows, o WebView2 já acompanha as versões atuais do sistema. No Linux, a distribuição precisa fornecer GTK3 e WebKit2GTK 4.1.

Ao abrir o aplicativo:

1. A janela desktop inicia o backend incorporado.
2. O aplicativo espera `/api/health` ficar disponível.
3. Dashboard, Providers, Routing, Requests, Playground e Settings são liberados.
4. Ao fechar a janela, somente o backend filho iniciado por ela é encerrado.

Uma segunda abertura não inicia outro backend nem concorre pelo SQLite. Se a porta `8000` estiver ocupada por outro processo, o aplicativo informa o conflito e não reutiliza esse processo.

## Dados locais

Os dados ficam fora da pasta de instalação:

- Windows: `%LOCALAPPDATA%\AI Gateway`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/ai-gateway`

O diretório contém SQLite, credenciais criptografadas, chave-mestra separada, logs operacionais e o backend extraído. Prompts e respostas não são persistidos; Requests registra somente IDs, provider/modelo, latência, status, erro sanitizado e tentativas.

## API

Principais endpoints:

```text
GET    /api/health
GET    /api/metrics
GET    /api/providers
POST   /api/providers
PUT    /api/providers/{id}
DELETE /api/providers/{id}
GET    /api/routing
PUT    /api/routing
GET    /api/requests
GET    /api/requests/{id}
POST   /api/generate
POST   /api/generate/stream
GET    /api/settings
PUT    /api/settings
```

No modo manual não há fallback. No modo automático, timeout, rate limit, quota, indisponibilidade e erros recuperáveis avançam para o próximo provider por prioridade. O streaming usa SSE e nunca mistura conteúdo de providers diferentes depois que o primeiro trecho é entregue.

## Desenvolvimento

Para compilar o projeto a partir do código-fonte, instale Go 1.27, Node 22, Python 3.12 e `uv`. Prepare o backend e indique explicitamente o Python do ambiente ao Wails:

```bash
cd backend
uv sync --locked --all-groups
cd ..
export AI_GATEWAY_BACKEND_EXECUTABLE="$PWD/backend/.venv/bin/python"
export AI_GATEWAY_BACKEND_ARGS_JSON='["-m","ai_gateway.sidecar"]'
export AI_GATEWAY_BACKEND_WORKDIR="$PWD/backend"
go run github.com/wailsapp/wails/v2/cmd/wails@v2.15.0 dev
```

Essas ferramentas são necessárias somente para desenvolvimento. Os artefatos publicados não dependem de Python, Node, Go ou WSL instalados.

## Gerar o aplicativo

Execute no próprio sistema operacional de destino:

```bash
cd backend
uv sync --locked --all-groups
uv run pyinstaller ai-gateway-backend.spec --clean --noconfirm
cd ..
```

No Linux, copie `backend/dist/ai-gateway-backend`; no Windows, copie `backend/dist/ai-gateway-backend.exe` para `internal/backendbinary/binaries/`. Depois gere a interface e o aplicativo:

```bash
cd ui
npm ci
npm run build
cd ..
go run github.com/wailsapp/wails/v2/cmd/wails@v2.15.0 build -clean
```

O executável final fica em `build/bin/`.

No Windows, `scripts/build-windows.ps1` também gera o instalador versionável `AIGatewaySetup.exe` na raiz.

## Verificar

Backend:

```bash
cd backend
uv sync --locked --all-groups
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Interface interna:

```bash
cd ui
npm ci
npm test
npm run build
```

Lifecycle Go:

```bash
go test ./internal/...
```

`wails.json` é necessário para o comando de build e não executa validações automáticas.
