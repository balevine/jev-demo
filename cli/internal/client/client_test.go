package client

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAuthStatusReadsTheMailbox(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/auth/status" {
			t.Errorf("asked for %s, wanted /auth/status", r.URL.Path)
		}
		w.Write([]byte(`{"authenticated":true,"email":"you@example.com"}`))
	}))
	defer server.Close()

	status, err := New(server.URL).AuthStatus()
	if err != nil {
		t.Fatalf("AuthStatus: %v", err)
	}
	if !status.Authenticated || status.Email != "you@example.com" {
		t.Errorf("got %+v", status)
	}
}

// A null email is what the server sends before anyone has logged in, and it has
// to decode rather than fail.
func TestAuthStatusAcceptsANullEmail(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"authenticated":false,"email":null}`))
	}))
	defer server.Close()

	status, err := New(server.URL).AuthStatus()
	if err != nil {
		t.Fatalf("AuthStatus: %v", err)
	}
	if status.Authenticated || status.Email != "" {
		t.Errorf("got %+v", status)
	}
}

// Every failure the server raises on purpose puts a finished sentence in
// `detail`, and that sentence is the whole point of showing an error at all.
func TestErrorUsesTheServerSentence(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"detail":"No Google OAuth client at /repo/credentials.json."}`))
	}))
	defer server.Close()

	_, err := New(server.URL).AuthStatus()

	var apiError *APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("got %T, wanted *APIError", err)
	}
	if apiError.StatusCode != http.StatusBadRequest {
		t.Errorf("got status %d", apiError.StatusCode)
	}
	if apiError.Error() != "No Google OAuth client at /repo/credentials.json." {
		t.Errorf("got %q", apiError.Error())
	}
}

// A request that does not validate comes back with a list in `detail` instead
// of a sentence. That is not worth picking apart, so it gets shown raw.
func TestErrorFallsBackToTheRawBody(t *testing.T) {
	body := `{"detail":[{"loc":["body","last"],"msg":"Input should be less than or equal to 100"}]}`
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		w.Write([]byte(body))
	}))
	defer server.Close()

	_, err := New(server.URL).AuthStatus()

	var apiError *APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("got %T, wanted *APIError", err)
	}
	if !strings.Contains(apiError.Error(), "422") || !strings.Contains(apiError.Error(), "less than or equal to 100") {
		t.Errorf("got %q", apiError.Error())
	}
}

func TestErrorWithNoBodyStillSaysTheStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	_, err := New(server.URL).AuthStatus()
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Errorf("got %v", err)
	}
}

// The server not being started is the likeliest first run stumble, so it gets
// its own error saying how to start it.
func TestUnreachableServerSaysHowToStartIt(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	address := server.URL
	server.Close()

	_, err := New(address).AuthStatus()

	var unreachable *UnreachableError
	if !errors.As(err, &unreachable) {
		t.Fatalf("got %T, wanted *UnreachableError", err)
	}
	if !strings.Contains(unreachable.Error(), address) || !strings.Contains(unreachable.Error(), "script/server") {
		t.Errorf("got %q", unreachable.Error())
	}
}

func TestTrailingSlashIsTrimmed(t *testing.T) {
	if got := New("http://127.0.0.1:8000/").Server(); got != "http://127.0.0.1:8000" {
		t.Errorf("got %q", got)
	}
}
