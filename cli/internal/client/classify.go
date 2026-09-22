package client

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

// DefaultLast is how many of the most recent threads a run covers when nothing
// narrows it. The server has the same default for anyone calling it directly.
// The number is repeated here so the run screen can show progress against it
// while the run is still going, because the real thread count only arrives with
// the totals at the end.
const DefaultLast = 25

// maxStreamLine caps one line of the classify stream. A line carries a subject,
// a snippet, and one entry per label, so this is far more than a real one needs
// and is only here to stop a runaway body from being read into memory.
const maxStreamLine = 1 << 20

// ClassifyRequest says which threads to look at, how sure Jev has to be before
// a label counts, and whether the server should write what it finds.
type ClassifyRequest struct {
	Last    int    `json:"last,omitempty"`
	Since   string `json:"since,omitempty"`
	Between string `json:"between,omitempty"`
	Query   string `json:"query,omitempty"`

	// Threshold is a pointer so an unset threshold stays distinct from a
	// deliberate 0, which means every label counts.
	Threshold *float64 `json:"threshold,omitempty"`

	Apply bool `json:"apply"`
}

// Usage is what one thread cost. Input tokens are billed and output is free.
type Usage struct {
	InputTokens int `json:"input_tokens"`
}

// LabelScore is what Jev thought of one label for one thread.
//
// Noul is nil when Jev did not answer that question, which is not the same as
// answering zero. Apply is the server's decision and this client never second
// guesses it.
type LabelScore struct {
	ID    string   `json:"id"`
	Name  string   `json:"name"`
	Noul  *float64 `json:"noul"`
	Apply bool     `json:"apply"`
}

// ThreadResult is one thread's line of the classify stream. A thread that went
// wrong carries what happened in Error and the rest of the run carries on.
type ThreadResult struct {
	ThreadID string       `json:"thread_id"`
	Subject  string       `json:"subject"`
	Sender   string       `json:"sender"`
	Date     string       `json:"date"`
	Snippet  string       `json:"snippet"`
	Labels   []LabelScore `json:"labels"`
	Applied  bool         `json:"applied"`
	Usage    Usage        `json:"usage"`
	Error    string       `json:"error"`
}

// Assigned is the labels that made the threshold, in the order they arrived.
func (result ThreadResult) Assigned() []LabelScore {
	assigned := make([]LabelScore, 0, len(result.Labels))
	for _, label := range result.Labels {
		if label.Apply {
			assigned = append(assigned, label)
		}
	}
	return assigned
}

// RunTotals is what a whole run came to, sent on the last line.
type RunTotals struct {
	Threads     int     `json:"threads"`
	InputTokens int     `json:"input_tokens"`
	CostUSD     float64 `json:"cost_usd"`
}

// StreamEvent is one line of the classify stream. Exactly one of Result,
// Totals, and Err is set. Raw is the line as it arrived, which is what `--json`
// prints so nothing is lost on the way through.
type StreamEvent struct {
	Result *ThreadResult
	Totals *RunTotals
	Raw    []byte
	Err    error
}

// Classify starts a run and returns a channel carrying one event per line. The
// channel closes when the server stops writing, which on a run that finished is
// after the totals line.
//
// Anything that goes wrong before the first line, such as a mailbox with no
// labels of its own, comes back as an error here. After that the response is
// already a 200 and there is no taking it back, so a later failure rides on an
// event instead.
//
// Cancelling ctx stops the reader. A caller that walks away without cancelling
// leaves it blocked.
func (client *Client) Classify(ctx context.Context, request ClassifyRequest) (<-chan StreamEvent, error) {
	response, err := client.send(ctx, http.MethodPost, "/classify", request)
	if err != nil {
		return nil, err
	}

	events := make(chan StreamEvent)
	go func() {
		defer close(events)
		defer response.Body.Close()
		readStream(ctx, response.Body, events)
	}()

	return events, nil
}

// ApplyResponse is what was written to one thread.
type ApplyResponse struct {
	ThreadID string   `json:"thread_id"`
	Applied  []string `json:"applied"`
}

// applyRequest is the body of a write. There is no field for removing a label
// because the server has no way to remove one.
type applyRequest struct {
	ThreadID string   `json:"thread_id"`
	LabelIDs []string `json:"label_ids"`
}

// Apply adds labels to one thread.
func (client *Client) Apply(threadID string, labelIDs []string) (ApplyResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), RequestTimeout)
	defer cancel()

	var applied ApplyResponse
	err := client.do(ctx, http.MethodPost, "/apply",
		applyRequest{ThreadID: threadID, LabelIDs: labelIDs}, &applied)
	return applied, err
}

// readStream turns an NDJSON body into one event per line.
func readStream(ctx context.Context, body io.Reader, events chan<- StreamEvent) {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(nil, maxStreamLine)

	for scanner.Scan() {
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			continue
		}
		if !send(ctx, events, decodeLine(line)) {
			return
		}
	}

	// A read that was cut short because the caller walked away is not worth
	// reporting. Nobody is left to read it.
	if err := scanner.Err(); err != nil && ctx.Err() == nil {
		send(ctx, events, StreamEvent{Err: fmt.Errorf("reading the classify stream: %w", err)})
	}
}

// decodeLine reads one line as either a thread or the totals that end a run.
// The totals line is the only one carrying a `totals` key, which is what tells
// the two apart.
func decodeLine(line []byte) StreamEvent {
	// The scanner reuses its buffer, so the line has to be copied before it is
	// handed to anyone who keeps it.
	raw := bytes.Clone(line)

	var envelope struct {
		Totals *RunTotals `json:"totals"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return StreamEvent{Raw: raw, Err: malformed(raw)}
	}
	if envelope.Totals != nil {
		return StreamEvent{Raw: raw, Totals: envelope.Totals}
	}

	var result ThreadResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return StreamEvent{Raw: raw, Err: malformed(raw)}
	}
	return StreamEvent{Raw: raw, Result: &result}
}

func malformed(raw []byte) error {
	return fmt.Errorf("the server sent a line that is not a result: %s", excerpt(raw))
}

// send hands over one event, and says whether there is any point sending more.
func send(ctx context.Context, events chan<- StreamEvent, event StreamEvent) bool {
	select {
	case events <- event:
		return true
	case <-ctx.Done():
		return false
	}
}
