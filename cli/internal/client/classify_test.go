package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// twoThreads is two thread lines and the totals line that ends a run.
const twoThreads = `{"thread_id":"t1","subject":"Invoice #4021","sender":"billing@acme.com","date":"2026-09-18T14:02:11Z","snippet":"Your September invoice","labels":[{"id":"Label_12","name":"Invoices","noul":0.93,"apply":true},{"id":"Label_19","name":"Recruiting","noul":0.02,"apply":false}],"applied":false,"usage":{"input_tokens":812},"error":null}
{"thread_id":"t2","subject":"Coffee?","sender":"Sam Reed <sam@example.com>","date":"2026-09-19T09:00:00Z","snippet":"Free Thursday","labels":[],"applied":false,"usage":{"input_tokens":304},"error":null}
{"totals":{"threads":2,"input_tokens":1116,"cost_usd":0.00004687}}
`

// collect drains a stream into a slice.
func collect(t *testing.T, body string) []StreamEvent {
	t.Helper()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/classify" {
			t.Errorf("asked for %s, wanted /classify", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte(body))
	}))
	defer server.Close()

	events, err := New(server.URL).Classify(t.Context(), ClassifyRequest{Last: 2})
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}

	var collected []StreamEvent
	for event := range events {
		collected = append(collected, event)
	}
	return collected
}

func TestStreamGivesOneEventPerLine(t *testing.T) {
	events := collect(t, twoThreads)

	if len(events) != 3 {
		t.Fatalf("got %d events, wanted 3", len(events))
	}
	for index, event := range events {
		if event.Err != nil {
			t.Fatalf("event %d: %v", index, event.Err)
		}
	}

	first := events[0].Result
	if first == nil {
		t.Fatal("the first event carried no thread")
	}
	if first.Subject != "Invoice #4021" || first.Usage.InputTokens != 812 {
		t.Errorf("got %+v", first)
	}

	assigned := first.Assigned()
	if len(assigned) != 1 || assigned[0].Name != "Invoices" {
		t.Errorf("got %+v, wanted only Invoices", assigned)
	}

	if events[2].Totals == nil || events[2].Totals.Threads != 2 {
		t.Errorf("got %+v, wanted the totals", events[2])
	}
}

// A server that stops writing mid line leaves no trailing newline, and that
// last line still has to arrive.
func TestStreamReadsALineWithNoTrailingNewline(t *testing.T) {
	events := collect(t, strings.TrimSuffix(twoThreads, "\n"))

	if len(events) != 3 {
		t.Fatalf("got %d events, wanted 3", len(events))
	}
	if events[2].Totals == nil {
		t.Errorf("the last line was dropped: %+v", events[2])
	}
}

// The raw line is what --json prints, so it has to survive the trip.
func TestStreamKeepsTheRawLine(t *testing.T) {
	events := collect(t, twoThreads)

	if !strings.Contains(string(events[0].Raw), `"thread_id":"t1"`) {
		t.Errorf("got %q", events[0].Raw)
	}
}

// A noul of zero and no noul at all mean different things, and decoding has to
// keep them apart.
func TestStreamKeepsAMissingNoulApartFromZero(t *testing.T) {
	body := `{"thread_id":"t1","subject":"s","labels":[{"id":"a","name":"A","noul":0.0,"apply":false},{"id":"b","name":"B","noul":null,"apply":false}],"usage":{"input_tokens":1}}` + "\n"
	events := collect(t, body)

	labels := events[0].Result.Labels
	if labels[0].Noul == nil || *labels[0].Noul != 0 {
		t.Errorf("a real zero decoded as %v", labels[0].Noul)
	}
	if labels[1].Noul != nil {
		t.Errorf("a missing noul decoded as %v", *labels[1].Noul)
	}
}

// A line that is not a result should be reported without taking the rest of the
// stream down with it.
func TestStreamReportsAMalformedLine(t *testing.T) {
	events := collect(t, "not json\n"+twoThreads)

	if len(events) != 4 {
		t.Fatalf("got %d events, wanted 4", len(events))
	}
	if events[0].Err == nil || !strings.Contains(events[0].Err.Error(), "not json") {
		t.Errorf("got %v", events[0].Err)
	}
	if events[1].Result == nil {
		t.Errorf("the stream stopped at the bad line")
	}
}

// Everything the server checks before it starts writing, such as a mailbox with
// no labels, has to come back as an error rather than on the stream.
func TestClassifyFailsBeforeTheStreamOpens(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"detail":"This mailbox has no labels of its own."}`))
	}))
	defer server.Close()

	events, err := New(server.URL).Classify(t.Context(), ClassifyRequest{})
	if events != nil {
		t.Error("a failed run still opened a stream")
	}

	var apiError *APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("got %T, wanted *APIError", err)
	}
	if apiError.Error() != "This mailbox has no labels of its own." {
		t.Errorf("got %q", apiError.Error())
	}
}

// Cancelling has to stop the reader rather than leave it blocked on a send.
func TestCancellingStopsTheStream(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(twoThreads))
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(t.Context())
	events, err := New(server.URL).Classify(ctx, ClassifyRequest{})
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}

	if first := <-events; first.Result == nil {
		t.Fatalf("got %+v, wanted a thread", first)
	}
	cancel()

	// Draining has to finish rather than hang. Whether the second line makes it
	// through before the cancel lands is a race, and either way is fine.
	for range events {
	}
}

func TestApplySendsOnlyLabelsToAdd(t *testing.T) {
	var body applyRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/apply" || r.Method != http.MethodPost {
			t.Errorf("got %s %s", r.Method, r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decoding the request: %v", err)
		}
		w.Write([]byte(`{"thread_id":"t1","applied":["Label_12"]}`))
	}))
	defer server.Close()

	applied, err := New(server.URL).Apply("t1", []string{"Label_12"})
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if body.ThreadID != "t1" || len(body.LabelIDs) != 1 || body.LabelIDs[0] != "Label_12" {
		t.Errorf("sent %+v", body)
	}
	if len(applied.Applied) != 1 {
		t.Errorf("got %+v", applied)
	}
}
