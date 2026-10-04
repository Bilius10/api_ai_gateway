# Empacotamento desktop

Gere o backend FastAPI com PyInstaller no sistema operacional de destino, copie o sidecar nativo para `internal/backendbinary/binaries/` e compile o aplicativo Wails.

PyInstaller não é cross-compiler. O executável Wails resultante extrai somente o backend do próprio sistema para o diretório de dados do usuário.
