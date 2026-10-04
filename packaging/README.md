# Desktop packaging

The release workflow builds the FastAPI backend with PyInstaller on each target operating system, injects that native sidecar into `internal/backendbinary/binaries/`, and then compiles the Wails application.

PyInstaller is intentionally executed on native Windows and Linux runners. It is not a cross-compiler. The resulting Wails executable extracts only its own embedded backend into the user's application-data directory.
