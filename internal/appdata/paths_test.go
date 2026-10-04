package appdata

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func TestResolveRootUsesNativeDataDirectories(t *testing.T) {
	env := map[string]string{"LOCALAPPDATA": `C:\Users\tester\AppData\Local`}
	getenv := func(key string) string { return env[key] }
	windowsRoot, err := resolveRoot("windows", func() (string, error) { return `C:\Users\tester`, nil }, getenv)
	if err != nil {
		t.Fatal(err)
	}
	if windowsRoot != filepath.Join(env["LOCALAPPDATA"], "AI Gateway") {
		t.Fatalf("unexpected Windows root: %s", windowsRoot)
	}
	env = map[string]string{"XDG_DATA_HOME": "/tmp/data"}
	linuxRoot, err := resolveRoot("linux", func() (string, error) { return "/home/tester", nil }, getenv)
	if err != nil {
		t.Fatal(err)
	}
	if linuxRoot != "/tmp/data/ai-gateway" {
		t.Fatalf("unexpected Linux root: %s", linuxRoot)
	}
}

func TestLoadOrCreateMasterKeyRejectsWrongLength(t *testing.T) {
	path := filepath.Join(t.TempDir(), "master.key")
	invalid := base64.URLEncoding.EncodeToString([]byte("too-short"))
	if err := os.WriteFile(path, []byte(invalid), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadOrCreateMasterKey(path); err == nil {
		t.Fatal("expected an invalid key length error")
	}
}

func TestLoadOrCreateMasterKeyPersistsFernetKey(t *testing.T) {
	path := filepath.Join(t.TempDir(), "master.key")
	first, err := LoadOrCreateMasterKey(path)
	if err != nil {
		t.Fatal(err)
	}
	second, err := LoadOrCreateMasterKey(path)
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Fatal("master key was not reused")
	}
	decoded, err := base64.URLEncoding.DecodeString(first)
	if err != nil || len(decoded) != 32 {
		t.Fatalf("invalid Fernet key: %v", err)
	}
}
