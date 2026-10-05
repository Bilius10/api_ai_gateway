package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAPIRequestBridgesDesktopToLocalAPI(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			t.Fatalf("method = %s", request.Method)
		}
		if request.URL.RequestURI() != "/api/generate?mode=test" {
			t.Fatalf("URI = %s", request.URL.RequestURI())
		}
		payload, err := io.ReadAll(request.Body)
		if err != nil {
			t.Fatal(err)
		}
		if string(payload) != `{"prompt":"hello"}` {
			t.Fatalf("body = %s", payload)
		}
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusAccepted)
		_, _ = writer.Write([]byte(`{"status":"queued"}`))
	}))
	defer server.Close()

	app := NewApp()
	app.apiBaseURL = server.URL
	response, err := app.APIRequest(http.MethodPost, "/api/generate?mode=test", `{"prompt":"hello"}`)
	if err != nil {
		t.Fatal(err)
	}
	if response.Status != http.StatusAccepted || response.Body != `{"status":"queued"}` {
		t.Fatalf("response = %#v", response)
	}
	if response.Headers["Content-Type"] != "application/json" {
		t.Fatalf("content type = %q", response.Headers["Content-Type"])
	}
}

func TestAPIRequestRejectsPathsOutsideAPI(t *testing.T) {
	app := NewApp()
	if _, err := app.APIRequest(http.MethodGet, "/admin", ""); err == nil {
		t.Fatal("expected path validation error")
	}
}

func TestAPIRequestLimitsResponseSize(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(writer, strings.Repeat("x", maxDesktopResponseBytes+1))
	}))
	defer server.Close()

	app := NewApp()
	app.apiBaseURL = server.URL
	if _, err := app.APIRequest(http.MethodGet, "/api/large", ""); err == nil {
		t.Fatal("expected response size error")
	}
}
