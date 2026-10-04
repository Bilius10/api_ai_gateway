package backendbinary

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMaterializeEmbeddedBackendWithoutExternalToolchain(t *testing.T) {
	root := t.TempDir()
	path, err := materialize(root, "backend-test", []byte("native-sidecar"))
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(filepath.Dir(path)) != root {
		t.Fatalf("backend escaped runtime directory: %s", path)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "native-sidecar" {
		t.Fatalf("unexpected materialized backend: %q, %v", data, err)
	}
	second, err := materialize(root, "backend-test", []byte("native-sidecar"))
	if err != nil || second != path {
		t.Fatalf("materialization is not stable: %s %v", second, err)
	}
}
