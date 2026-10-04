package backendprocess

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

const (
	StateStopped  = "stopped"
	StateStarting = "starting"
	StateReady    = "ready"
	StateStopping = "stopping"
	StateError    = "error"
)

var (
	ErrAlreadyRunning = errors.New("backend is already running")
	ErrPortInUse      = errors.New("backend port is already in use")
)

type Command struct {
	Path string
	Args []string
	Dir  string
}

type Config struct {
	Command        Command
	HealthURL      string
	ListenAddress  string
	StartupTimeout time.Duration
	StopTimeout    time.Duration
	LogPath        string
	Environment    map[string]string
}

type Status struct {
	State   string `json:"state"`
	Running bool   `json:"running"`
	Ready   bool   `json:"ready"`
	Message string `json:"message"`
	URL     string `json:"url"`
	PID     int    `json:"pid"`
}

type Manager struct {
	mu     sync.RWMutex
	config Config
	status Status
	cmd    *exec.Cmd
	done   chan error
}

func New(config Config) *Manager {
	if config.StartupTimeout <= 0 {
		config.StartupTimeout = 25 * time.Second
	}
	if config.StopTimeout <= 0 {
		config.StopTimeout = 8 * time.Second
	}
	return &Manager{
		config: config,
		status: Status{State: StateStopped, Message: "Backend is stopped.", URL: apiRoot(config.HealthURL)},
	}
}

func (m *Manager) Status() Status {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.status
}

func (m *Manager) Start(ctx context.Context) error {
	m.mu.Lock()
	if m.cmd != nil || m.status.State == StateStarting || m.status.State == StateReady {
		m.mu.Unlock()
		return ErrAlreadyRunning
	}
	m.status = Status{State: StateStarting, Running: false, Ready: false, Message: "Starting local API…", URL: apiRoot(m.config.HealthURL)}
	m.mu.Unlock()

	if portUnavailable(m.config.ListenAddress) {
		err := fmt.Errorf("%w: %s", ErrPortInUse, m.config.ListenAddress)
		m.setError("Port 8000 is occupied by another process.")
		return err
	}
	if strings.TrimSpace(m.config.Command.Path) == "" {
		err := errors.New("backend executable is not configured")
		m.setError(err.Error())
		return err
	}
	logWriter, closeLog, err := m.openLog()
	if err != nil {
		m.setError("Backend log could not be opened.")
		return err
	}
	cmd := exec.Command(m.config.Command.Path, m.config.Command.Args...)
	cmd.Dir = m.config.Command.Dir
	cmd.Env = mergeEnvironment(os.Environ(), m.config.Environment)
	cmd.Stdout = logWriter
	cmd.Stderr = logWriter
	configureProcess(cmd)
	if err := cmd.Start(); err != nil {
		closeLog()
		m.setError("Packaged backend could not be started.")
		return fmt.Errorf("start backend: %w", err)
	}
	done := make(chan error, 1)
	m.mu.Lock()
	m.cmd = cmd
	m.done = done
	m.status = Status{State: StateStarting, Running: true, Message: "Waiting for local API…", URL: apiRoot(m.config.HealthURL), PID: cmd.Process.Pid}
	m.mu.Unlock()
	go func() {
		err := cmd.Wait()
		closeLog()
		done <- err
		close(done)
		m.recordExit(cmd, err)
	}()

	deadline := time.NewTimer(m.config.StartupTimeout)
	defer deadline.Stop()
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			_ = m.Stop(context.Background())
			m.setError("Backend startup was cancelled.")
			return ctx.Err()
		case err := <-done:
			if err == nil {
				err = errors.New("backend exited before becoming ready")
			}
			m.setError("Backend exited before becoming ready.")
			return err
		case <-deadline.C:
			_ = m.Stop(context.Background())
			m.setError("Backend did not become ready in time.")
			return errors.New("backend readiness timeout")
		case <-ticker.C:
			if healthReady(m.config.HealthURL) {
				m.mu.Lock()
				if m.cmd == cmd {
					m.status = Status{State: StateReady, Running: true, Ready: true, Message: "Local API is ready.", URL: apiRoot(m.config.HealthURL), PID: cmd.Process.Pid}
				}
				m.mu.Unlock()
				return nil
			}
		}
	}
}

func (m *Manager) Stop(ctx context.Context) error {
	m.mu.Lock()
	cmd := m.cmd
	done := m.done
	if cmd == nil || cmd.Process == nil {
		m.status = Status{State: StateStopped, Message: "Backend is stopped.", URL: apiRoot(m.config.HealthURL)}
		m.mu.Unlock()
		return nil
	}
	m.status = Status{State: StateStopping, Running: true, Message: "Stopping local API…", URL: apiRoot(m.config.HealthURL), PID: cmd.Process.Pid}
	m.mu.Unlock()

	_ = terminateProcess(cmd)
	timer := time.NewTimer(m.config.StopTimeout)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		_ = forceKillProcess(cmd)
		return ctx.Err()
	case <-timer.C:
		_ = forceKillProcess(cmd)
		select {
		case <-done:
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
			return errors.New("backend process did not stop")
		}
	case <-done:
	}
	m.mu.Lock()
	if m.cmd == cmd {
		m.cmd = nil
		m.done = nil
		m.status = Status{State: StateStopped, Message: "Backend is stopped.", URL: apiRoot(m.config.HealthURL)}
	}
	m.mu.Unlock()
	return nil
}

func (m *Manager) recordExit(cmd *exec.Cmd, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.cmd != cmd {
		return
	}
	m.cmd = nil
	m.done = nil
	if m.status.State == StateStopping {
		m.status = Status{State: StateStopped, Message: "Backend is stopped.", URL: apiRoot(m.config.HealthURL)}
		return
	}
	message := "Backend stopped unexpectedly."
	if err != nil {
		message = "Backend stopped unexpectedly; inspect the local backend log."
	}
	m.status = Status{State: StateError, Message: message, URL: apiRoot(m.config.HealthURL)}
}

func (m *Manager) setError(message string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.status = Status{State: StateError, Message: message, URL: apiRoot(m.config.HealthURL)}
}

func (m *Manager) openLog() (io.Writer, func(), error) {
	if m.config.LogPath == "" {
		return io.Discard, func() {}, nil
	}
	if err := os.MkdirAll(filepath.Dir(m.config.LogPath), 0o700); err != nil {
		return nil, func() {}, fmt.Errorf("create log directory: %w", err)
	}
	file, err := os.OpenFile(m.config.LogPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, func() {}, fmt.Errorf("open backend log: %w", err)
	}
	return file, func() { _ = file.Close() }, nil
}

func portUnavailable(address string) bool {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return true
	}
	_ = listener.Close()
	return false
}

func healthReady(url string) bool {
	client := &http.Client{Timeout: 600 * time.Millisecond}
	response, err := client.Get(url)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode == http.StatusOK
}

func mergeEnvironment(base []string, overrides map[string]string) []string {
	values := make(map[string]string, len(base)+len(overrides))
	originalKeys := make(map[string]string, len(base)+len(overrides))
	normalize := func(key string) string {
		if runtime.GOOS == "windows" {
			return strings.ToUpper(key)
		}
		return key
	}
	for _, entry := range base {
		parts := strings.SplitN(entry, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := normalize(parts[0])
		values[key] = parts[1]
		originalKeys[key] = parts[0]
	}
	for key, value := range overrides {
		normalized := normalize(key)
		values[normalized] = value
		originalKeys[normalized] = key
	}
	result := make([]string, 0, len(values))
	for key, value := range values {
		result = append(result, originalKeys[key]+"="+value)
	}
	return result
}

func apiRoot(healthURL string) string {
	return strings.TrimSuffix(healthURL, "/api/health")
}
