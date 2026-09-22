package client

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestLabelsCarryTheirDescriptions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/labels" {
			t.Errorf("asked for %s, wanted /labels", r.URL.Path)
		}
		w.Write([]byte(`[{"id":"Label_12","name":"Invoices","description":"receipts and bills"},
		                 {"id":"Label_19","name":"Recruiting","description":""}]`))
	}))
	defer server.Close()

	labels, err := New(server.URL).Labels()
	if err != nil {
		t.Fatalf("Labels: %v", err)
	}
	if len(labels) != 2 {
		t.Fatalf("got %d labels", len(labels))
	}
	if labels[0].Description != "receipts and bills" || labels[1].Description != "" {
		t.Errorf("got %+v", labels)
	}
}

// A mailbox with no labels of its own is an empty list rather than a failure,
// and the settings screen is the one that has something to say about it.
func TestLabelsAcceptsAnEmptyMailbox(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`[]`))
	}))
	defer server.Close()

	labels, err := New(server.URL).Labels()
	if err != nil {
		t.Fatalf("Labels: %v", err)
	}
	if len(labels) != 0 {
		t.Errorf("got %+v", labels)
	}
}

func TestSaveDescriptionsSendsEveryRow(t *testing.T) {
	var method, path string
	var sent map[string]string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		json.Unmarshal(raw, &sent)
		w.Write([]byte(`{"Label_12":"receipts and bills"}`))
	}))
	defer server.Close()

	kept, err := New(server.URL).SaveDescriptions(map[string]string{
		"Label_12": "receipts and bills",
		"Label_19": "",
	})
	if err != nil {
		t.Fatalf("SaveDescriptions: %v", err)
	}

	if method != http.MethodPut || path != "/label-descriptions" {
		t.Errorf("sent %s %s", method, path)
	}
	if len(sent) != 2 || sent["Label_19"] != "" {
		t.Errorf("got %+v, wanted the blank row to travel", sent)
	}
	if len(kept) != 1 {
		t.Errorf("got %+v, wanted only what the server kept", kept)
	}
}

// A nil map encodes as null, which the server will not take, so it has to go
// out as an empty object instead.
func TestSaveDescriptionsSendsAnObjectForNothing(t *testing.T) {
	var body string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		body = string(raw)
		w.Write([]byte(`{}`))
	}))
	defer server.Close()

	if _, err := New(server.URL).SaveDescriptions(nil); err != nil {
		t.Fatalf("SaveDescriptions: %v", err)
	}
	if body != "{}" {
		t.Errorf("got %q", body)
	}
}
