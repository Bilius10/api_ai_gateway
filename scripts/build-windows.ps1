[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backendBinary = Join-Path $repo 'internal\backendbinary\binaries\ai-gateway-backend.exe'
$application = Join-Path $repo 'build\bin\api-ai-gateway.exe'
$payload = Join-Path $repo 'cmd\ai-gateway-installer\payload\AI Gateway.exe'
$setup = Join-Path $repo 'AIGatewaySetup.exe'

Push-Location $repo
try {
    Push-Location 'backend'
    try {
        & uv sync --locked --all-groups
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependency setup failed' }
        & uv run pyinstaller ai-gateway-backend.spec --clean --noconfirm
        if ($LASTEXITCODE -ne 0) { throw 'Backend build failed' }
        Copy-Item -LiteralPath 'dist\ai-gateway-backend.exe' -Destination $backendBinary -Force
    } finally { Pop-Location }

    Push-Location 'ui'
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw 'UI dependency setup failed' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'UI build failed' }
    } finally { Pop-Location }

    & go run github.com/wailsapp/wails/v2/cmd/wails@v2.15.0 build -clean -trimpath -webview2 embed -platform windows/amd64
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }

    Copy-Item -LiteralPath $application -Destination $payload -Force
    & go build -a -trimpath -ldflags '-H windowsgui -s -w' -o $setup ./cmd/ai-gateway-installer
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} finally {
    Remove-Item -LiteralPath $payload -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backendBinary -Force -ErrorAction SilentlyContinue
    Pop-Location
}
