package backendprocess

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"testing"
	"time"
)

func TestBackendHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_BACKEND_HELPER") != "1" {
		return
	}
	port := os.Getenv("TEST_BACKEND_PORT")
	mux := http.NewServeMux()
	mux.HandleFunc("/api/health", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"status":"ok"}`))
	})
	server := &http.Server{Addr: "127.0.0.1:" + port, Handler: mux, ReadHeaderTimeout: time.Second}
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		os.Exit(2)
	}
	os.Exit(0)
}

func TestManagerStartsOnceBecomesReadyAndStopsOwnedProcess(t *testing.T) {
	port := freePort(t)
	manager := New(testConfig(t, port))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := manager.Start(ctx); err != nil {
		t.Fatal(err)
	}
	status := manager.Status()
	if !status.Ready || !status.Running || status.PID == 0 {
		t.Fatalf("unexpected ready status: %+v", status)
	}
	if err := manager.Start(ctx); !errors.Is(err, ErrAlreadyRunning) {
		t.Fatalf("second start should be rejected, got %v", err)
	}
	if err := manager.Stop(ctx); err != nil {
		t.Fatal(err)
	}
	if status := manager.Status(); status.State != StateStopped || status.Running {
		t.Fatalf("unexpected stopped status: %+v", status)
	}
}

func TestManagerRejectsUnknownProcessOnConfiguredPort(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	manager := New(testConfig(t, port))
	err = manager.Start(context.Background())
	if !errors.Is(err, ErrPortInUse) {
		t.Fatalf("expected port conflict, got %v", err)
	}
	if status := manager.Status(); status.State != StateError {
		t.Fatalf("unexpected port conflict status: %+v", status)
	}
}

func testConfig(t *testing.T, port int) Config {
	t.Helper()
	portValue := strconv.Itoa(port)
	return Config{
		Command:        Command{Path: os.Args[0], Args: []string{"-test.run=TestBackendHelperProcess"}},
		HealthURL:      fmt.Sprintf("http://127.0.0.1:%d/api/health", port),
		ListenAddress:  fmt.Sprintf("127.0.0.1:%d", port),
		StartupTimeout: 4 * time.Second,
		StopTimeout:    2 * time.Second,
		LogPath:        t.TempDir() + "/backend.log",
		Environment: map[string]string{
			"GO_WANT_BACKEND_HELPER": "1",
			"TEST_BACKEND_PORT":      portValue,
		},
	}
}

func freePort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	return port
}
