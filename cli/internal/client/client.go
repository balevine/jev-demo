// Package client talks to the jev-demo server over HTTP.
//
// The CLI holds no Gmail or Jev credentials and makes no decisions of its own.
// Everything here is a request against the server and a decode of what comes
// back, so the only logic in this package is turning a failure into something
// worth reading.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// DefaultServer is where the server listens unless --server says otherwise.
const DefaultServer = "http://127.0.0.1:8000"

// RequestTimeout covers every call except login.
const RequestTimeout = 30 * time.Second

// LoginTimeout covers /auth/login, which blocks while somebody works through
// Google's consent screen in a browser. Thirty seconds is nowhere near enough.
const LoginTimeout = 5 * time.Minute

// maxBodyExcerpt caps how much of an unrecognized error body gets printed.
const maxBodyExcerpt = 300

// Client is an HTTP client for one jev-demo server.
type Client struct {
	baseURL string
	http    *http.Client
}

// New returns a client pointed at baseURL.
func New(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		// No Timeout is set on the client itself. Each call carries its own
		// deadline through a context, because login waits on a person and
		// everything else should not.
		http: &http.Client{},
	}
}

// Server is the address this client is pointed at.
func (client *Client) Server() string {
	return client.baseURL
}

// AuthStatus is what the server knows about the Gmail connection.
type AuthStatus struct {
	Authenticated bool   `json:"authenticated"`
	Email         string `json:"email,omitempty"`
}

// AuthStatus asks whether Gmail is connected, and to which mailbox.
func (client *Client) AuthStatus() (AuthStatus, error) {
	ctx, cancel := context.WithTimeout(context.Background(), RequestTimeout)
	defer cancel()

	var status AuthStatus
	err := client.do(ctx, http.MethodGet, "/auth/status", nil, &status)
	return status, err
}

// AuthLogin runs the consent flow on the server and returns the mailbox it
// connected to. It blocks until consent finishes in the browser.
func (client *Client) AuthLogin() (AuthStatus, error) {
	ctx, cancel := context.WithTimeout(context.Background(), LoginTimeout)
	defer cancel()

	var status AuthStatus
	err := client.do(ctx, http.MethodPost, "/auth/login", nil, &status)
	return status, err
}

// send makes one request and hands back the response with its body still open,
// for a caller that wants to read it itself. Every failure a request can have
// before its body matters is turned into an error here, so the same problem
// always reads the same way whoever asked.
func (client *Client) send(ctx context.Context, method, path string, body any) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("encoding request body: %w", err)
		}
		reader = bytes.NewReader(encoded)
	}

	request, err := http.NewRequestWithContext(ctx, method, client.baseURL+path, reader)
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := client.http.Do(request)
	if err != nil {
		// A cancelled context means the server was reached and then ran out of
		// time, which is a different problem from nothing answering at all.
		if ctx.Err() != nil {
			return nil, fmt.Errorf("the server did not answer %s in time", path)
		}
		return nil, &UnreachableError{Server: client.baseURL, Err: err}
	}

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		defer response.Body.Close()
		raw, readErr := io.ReadAll(response.Body)
		if readErr != nil {
			return nil, fmt.Errorf("reading error response: %w", readErr)
		}
		return nil, newAPIError(response.StatusCode, raw)
	}

	return response, nil
}

// do sends one request and decodes a successful answer into result. Pass a nil
// body for a request that carries none, and a nil result to discard the answer.
func (client *Client) do(ctx context.Context, method, path string, body, result any) error {
	response, err := client.send(ctx, method, path, body)
	if err != nil {
		return err
	}
	defer response.Body.Close()

	if result != nil {
		if err := json.NewDecoder(response.Body).Decode(result); err != nil && !errors.Is(err, io.EOF) {
			return fmt.Errorf("decoding response: %w", err)
		}
	}

	return nil
}

// UnreachableError means nothing answered at the server address. On a first run
// that is almost always the server not being started yet.
type UnreachableError struct {
	Server string
	Err    error
}

func (unreachable *UnreachableError) Error() string {
	return fmt.Sprintf(
		"Nothing is answering at %s. Start the server with `script/server` from the repo root, or point somewhere else with --server.",
		unreachable.Server,
	)
}

func (unreachable *UnreachableError) Unwrap() error {
	return unreachable.Err
}

// APIError is a non-success answer from the server.
type APIError struct {
	StatusCode int
	Message    string
	RawBody    []byte
}

func (apiError *APIError) Error() string {
	return apiError.Message
}

// newAPIError reads whatever the server said into something worth printing.
//
// The server writes a plain sentence into `detail` for every failure it raises
// on purpose, and those sentences are already written for the person reading
// them. Anything else, including the field by field list that comes back from a
// request that does not validate, is shown raw rather than picked apart.
func newAPIError(statusCode int, raw []byte) *APIError {
	apiError := &APIError{StatusCode: statusCode, RawBody: raw}

	var envelope struct {
		Detail json.RawMessage `json:"detail"`
	}
	var sentence string
	if json.Unmarshal(raw, &envelope) == nil &&
		json.Unmarshal(envelope.Detail, &sentence) == nil &&
		sentence != "" {
		apiError.Message = sentence
		return apiError
	}

	body := excerpt(raw)
	if body == "" {
		apiError.Message = fmt.Sprintf("The server answered %d with an empty body.", statusCode)
		return apiError
	}
	apiError.Message = fmt.Sprintf("The server answered %d: %s", statusCode, body)
	return apiError
}

// excerpt is as much of a body as is worth putting in front of somebody.
func excerpt(raw []byte) string {
	body := strings.TrimSpace(string(raw))
	if len(body) > maxBodyExcerpt {
		return body[:maxBodyExcerpt] + "…"
	}
	return body
}
