package backendproxy

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

const unavailableMessage = "Local API is unavailable."

func New(target string) (func(http.Handler) http.Handler, error) {
	targetURL, err := url.Parse(target)
	if err != nil {
		return nil, fmt.Errorf("parse backend proxy target: %w", err)
	}
	if targetURL.Scheme == "" || targetURL.Host == "" {
		return nil, fmt.Errorf("backend proxy target must include scheme and host")
	}

	proxy := httputil.NewSingleHostReverseProxy(targetURL)
	proxy.ErrorHandler = func(writer http.ResponseWriter, _ *http.Request, _ error) {
		http.Error(writer, unavailableMessage, http.StatusServiceUnavailable)
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			if isAPIPath(request.URL.Path) {
				proxy.ServeHTTP(writer, request)
				return
			}
			next.ServeHTTP(writer, request)
		})
	}, nil
}

func isAPIPath(path string) bool {
	return path == "/api" || strings.HasPrefix(path, "/api/")
}
