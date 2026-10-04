package backendbinary

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

// Build jobs place the platform-specific PyInstaller executable in this folder
// before compiling the Wails application.
//
//go:embed binaries/*
var binaries embed.FS

func Materialize(runtimeDir string) (string, error) {
	name := "ai-gateway-backend"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	data, err := binaries.ReadFile("binaries/" + name)
	if err != nil {
		return "", fmt.Errorf("packaged backend is missing for %s: %w", runtime.GOOS, err)
	}
	return materialize(runtimeDir, name, data)
}

func materialize(runtimeDir, name string, data []byte) (string, error) {
	if len(data) == 0 {
		return "", fmt.Errorf("packaged backend is empty")
	}
	sum := sha256.Sum256(data)
	directory := filepath.Join(runtimeDir, fmt.Sprintf("%x", sum[:8]))
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return "", fmt.Errorf("create backend runtime directory: %w", err)
	}
	path := filepath.Join(directory, name)
	if existing, err := os.ReadFile(path); err == nil && bytes.Equal(existing, data) {
		if err := os.Chmod(path, 0o700); err != nil {
			return "", fmt.Errorf("mark packaged backend executable: %w", err)
		}
		return path, nil
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, data, 0o700); err != nil {
		return "", fmt.Errorf("write packaged backend: %w", err)
	}
	if err := os.Rename(temporary, path); err != nil {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("activate packaged backend: %w", err)
	}
	if err := os.Chmod(path, 0o700); err != nil {
		return "", fmt.Errorf("mark packaged backend executable: %w", err)
	}
	return path, nil
}
