package backendproxy

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMiddlewareRoutesAPIToBackend(t *testing.T) {
	var receivedPath string
	var receivedHeader string
	backend := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		receivedPath = request.URL.RequestURI()
		receivedHeader = request.Header.Get("X-Gateway-Test")
		writer.Header().Set("X-Backend", "ready")
		writer.WriteHeader(http.StatusCreated)
		_, _ = writer.Write([]byte("proxied"))
	}))
	defer backend.Close()

	middleware, err := New(backend.URL)
	if err != nil {
		t.Fatal(err)
	}
	nextCalled := false
	handler := middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		nextCalled = true
		http.NotFound(writer, request)
	}))
	request := httptest.NewRequest(http.MethodPost, "http://wails.localhost/api/generate?mode=test", nil)
	request.Header.Set("X-Gateway-Test", "preserved")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if nextCalled {
		t.Fatal("asset handler was called for an API request")
	}
	if receivedPath != "/api/generate?mode=test" {
		t.Fatalf("unexpected backend path: %q", receivedPath)
	}
	if receivedHeader != "preserved" {
		t.Fatalf("request header was not forwarded: %q", receivedHeader)
	}
	if response.Code != http.StatusCreated {
		t.Fatalf("unexpected status: %d", response.Code)
	}
	if response.Header().Get("X-Backend") != "ready" {
		t.Fatal("backend response header was not forwarded")
	}
	if response.Body.String() != "proxied" {
		t.Fatalf("unexpected body: %q", response.Body.String())
	}
}

func TestMiddlewareLeavesNonAPIRequestsToAssetHandler(t *testing.T) {
	middleware, err := New("http://127.0.0.1:8000")
	if err != nil {
		t.Fatal(err)
	}
	handler := middleware(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusAccepted)
		_, _ = writer.Write([]byte("asset"))
	}))
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://wails.localhost/index.html", nil))
	if response.Code != http.StatusAccepted {
		t.Fatalf("unexpected status: %d", response.Code)
	}
	if response.Body.String() != "asset" {
		t.Fatalf("unexpected body: %q", response.Body.String())
	}
}

func TestMiddlewareReturnsUnavailableWithoutLeakingTransportError(t *testing.T) {
	backend := httptest.NewServer(http.NotFoundHandler())
	target := backend.URL
	backend.Close()

	middleware, err := New(target)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	middleware(http.NotFoundHandler()).ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://wails.localhost/api/health", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("unexpected status: %d", response.Code)
	}
	if response.Body.String() != "Local API is unavailable.\n" {
		t.Fatalf("unexpected body: %q", response.Body.String())
	}
}

func TestNewRejectsInvalidTarget(t *testing.T) {
	if _, err := New("not-a-url"); err == nil {
		t.Fatal("expected invalid target error")
	}
}
