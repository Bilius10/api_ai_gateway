//go:build desktop

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/Bilius10/api_ai_gateway/internal/appdata"
	"github.com/Bilius10/api_ai_gateway/internal/backendbinary"
	"github.com/Bilius10/api_ai_gateway/internal/backendprocess"
	"github.com/wailsapp/wails/v2/pkg/options"
	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

type App struct {
	mu         sync.RWMutex
	ctx        context.Context
	backend    *backendprocess.Manager
	startupErr error
}

func NewApp() *App {
	return &App{}
}

func (a *App) initialize() {
	paths, err := appdata.Resolve()
	if err != nil {
		a.startupErr = err
		return
	}
	if err := appdata.Ensure(paths); err != nil {
		a.startupErr = err
		return
	}
	masterKey, err := appdata.LoadOrCreateMasterKey(paths.MasterKey)
	if err != nil {
		a.startupErr = err
		return
	}
	command, err := resolveBackendCommand(paths)
	if err != nil {
		a.startupErr = err
		return
	}
	config := backendprocess.Config{
		Command:        command,
		HealthURL:      "http://127.0.0.1:8000/api/health",
		ListenAddress:  "0.0.0.0:8000",
		StartupTimeout: 25 * time.Second,
		StopTimeout:    8 * time.Second,
		LogPath:        filepath.Join(paths.Logs, "backend.log"),
		Environment: map[string]string{
			"AI_GATEWAY_DATABASE_PATH":   paths.Database,
			"AI_GATEWAY_CREDENTIAL_FILE": paths.Credentials,
			"AI_GATEWAY_CODEX_WORKSPACE": paths.CodexWorkspace,
			"AI_GATEWAY_MASTER_KEY":      masterKey,
			"AI_GATEWAY_HOST":            "0.0.0.0",
			"AI_GATEWAY_PORT":            "8000",
			"AI_GATEWAY_CORS_ORIGINS":    `["http://wails.localhost","wails://wails.localhost","http://localhost"]`,
		},
	}
	a.backend = backendprocess.New(config)
}

func resolveBackendCommand(paths appdata.Paths) (backendprocess.Command, error) {
	if executable := os.Getenv("AI_GATEWAY_BACKEND_EXECUTABLE"); executable != "" {
		var args []string
		if raw := os.Getenv("AI_GATEWAY_BACKEND_ARGS_JSON"); raw != "" {
			if err := json.Unmarshal([]byte(raw), &args); err != nil {
				return backendprocess.Command{}, fmt.Errorf("invalid AI_GATEWAY_BACKEND_ARGS_JSON: %w", err)
			}
		}
		return backendprocess.Command{Path: executable, Args: args, Dir: os.Getenv("AI_GATEWAY_BACKEND_WORKDIR")}, nil
	}
	executable, err := backendbinary.Materialize(paths.Runtime)
	if err != nil {
		return backendprocess.Command{}, err
	}
	return backendprocess.Command{Path: executable, Dir: paths.Root}, nil
}

func (a *App) startup(ctx context.Context) {
	a.mu.Lock()
	a.ctx = ctx
	a.initialize()
	backend := a.backend
	startupErr := a.startupErr
	a.mu.Unlock()
	if startupErr != nil || backend == nil {
		return
	}
	go func() { _ = backend.Start(context.Background()) }()
}

func (a *App) shutdown(_ context.Context) {
	a.mu.RLock()
	backend := a.backend
	a.mu.RUnlock()
	if backend == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	_ = backend.Stop(ctx)
}

func (a *App) onSecondInstanceLaunch(_ options.SecondInstanceData) {
	a.mu.RLock()
	ctx := a.ctx
	a.mu.RUnlock()
	if ctx == nil {
		return
	}
	wailsruntime.WindowShow(ctx)
	wailsruntime.WindowUnminimise(ctx)
}

func (a *App) BackendStatus() backendprocess.Status {
	a.mu.RLock()
	startupErr := a.startupErr
	backend := a.backend
	a.mu.RUnlock()
	if startupErr != nil {
		return backendprocess.Status{State: backendprocess.StateError, Message: "AI Gateway could not prepare its local application data.", URL: "http://127.0.0.1:8000"}
	}
	if backend == nil {
		return backendprocess.Status{State: backendprocess.StateError, Message: "backend manager is unavailable", URL: "http://127.0.0.1:8000"}
	}
	return backend.Status()
}

func (a *App) RestartBackend() error {
	a.mu.RLock()
	backend := a.backend
	a.mu.RUnlock()
	if backend == nil {
		return fmt.Errorf("backend manager is unavailable")
	}
	stopCtx, stopCancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer stopCancel()
	if err := backend.Stop(stopCtx); err != nil {
		return fmt.Errorf("stop backend before restart: %w", err)
	}
	startCtx, startCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer startCancel()
	if err := backend.Start(startCtx); err != nil {
		return fmt.Errorf("restart backend: %w", err)
	}
	return nil
}
