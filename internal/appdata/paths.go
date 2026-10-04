package appdata

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

type Paths struct {
	Root           string
	Database       string
	Credentials    string
	CodexWorkspace string
	Logs           string
	Runtime        string
	MasterKey      string
}

func Resolve() (Paths, error) {
	root, err := resolveRoot(runtime.GOOS, os.UserHomeDir, os.Getenv)
	if err != nil {
		return Paths{}, err
	}
	return Paths{
		Root:           root,
		Database:       filepath.Join(root, "gateway.db"),
		Credentials:    filepath.Join(root, "secrets.enc"),
		CodexWorkspace: filepath.Join(root, "codex-runs"),
		Logs:           filepath.Join(root, "logs"),
		Runtime:        filepath.Join(root, "runtime"),
		MasterKey:      filepath.Join(root, "master.key"),
	}, nil
}

func resolveRoot(goos string, home func() (string, error), getenv func(string) string) (string, error) {
	if override := strings.TrimSpace(getenv("AI_GATEWAY_DATA_DIR")); override != "" {
		return filepath.Abs(override)
	}
	if goos == "windows" {
		if local := strings.TrimSpace(getenv("LOCALAPPDATA")); local != "" {
			return filepath.Join(local, "AI Gateway"), nil
		}
	}
	if dataHome := strings.TrimSpace(getenv("XDG_DATA_HOME")); dataHome != "" {
		return filepath.Join(dataHome, "ai-gateway"), nil
	}
	userHome, err := home()
	if err != nil {
		return "", fmt.Errorf("resolve user data directory: %w", err)
	}
	return filepath.Join(userHome, ".local", "share", "ai-gateway"), nil
}

func Ensure(paths Paths) error {
	for _, directory := range []string{paths.Root, paths.CodexWorkspace, paths.Logs, paths.Runtime} {
		if err := os.MkdirAll(directory, 0o700); err != nil {
			return fmt.Errorf("create application data directory: %w", err)
		}
	}
	return nil
}

func LoadOrCreateMasterKey(path string) (string, error) {
	if data, err := os.ReadFile(path); err == nil {
		value := strings.TrimSpace(string(data))
		decoded, decodeErr := base64.URLEncoding.DecodeString(value)
		if decodeErr != nil {
			return "", fmt.Errorf("master key file is invalid: %w", decodeErr)
		}
		if len(decoded) != 32 {
			return "", fmt.Errorf("master key file is invalid: expected 32 bytes, got %d", len(decoded))
		}
		return value, nil
	} else if !os.IsNotExist(err) {
		return "", fmt.Errorf("read master key: %w", err)
	}
	value := make([]byte, 32)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate master key: %w", err)
	}
	encoded := base64.URLEncoding.EncodeToString(value)
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, []byte(encoded+"\n"), 0o600); err != nil {
		return "", fmt.Errorf("write master key: %w", err)
	}
	if err := os.Rename(temporary, path); err != nil {
		_ = os.Remove(temporary)
		return "", fmt.Errorf("activate master key: %w", err)
	}
	return encoded, nil
}
