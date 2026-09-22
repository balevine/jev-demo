package ui

import (
	"context"
	"fmt"

	"charm.land/bubbles/v2/key"
	tea "charm.land/bubbletea/v2"

	"github.com/balevine/jev-demo/cli/internal/client"
)

// jevInputTokenCost is dollars per million input tokens. Output tokens are
// free. The server sends the real figure with the totals at the end of a run,
// and this is only here so the footer can show a running number before that
// line arrives.
const jevInputTokenCost = 0.042

// runRow is one thread on the list, and whether its labels have been written.
type runRow struct {
	result  client.ThreadResult
	applied bool
}

// RunModel is the jev run screen. One row per thread, appended as the server
// answers, over a footer carrying progress, whether anything has been written,
// what the run has cost so far, and the keys.
type RunModel struct {
	api    *client.Client
	events <-chan client.StreamEvent
	keys   runKeys
	styles Styles

	width  int
	height int

	rows      []runRow
	expected  int
	tokens    int
	totals    *client.RunTotals
	streaming bool

	applying bool
	notice   string
	failure  string
}

// Run streams a classification into the list screen and blocks until whoever is
// watching quits.
func Run(ctx context.Context, api *client.Client, request client.ClassifyRequest) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	// The stream is opened before the screen is, so a mailbox with no labels or
	// a server that is not there prints plainly instead of flashing a TUI.
	events, err := api.Classify(ctx, request)
	if err != nil {
		return err
	}

	model := RunModel{
		api:    api,
		events: events,
		keys:   newRunKeys(),
		// A palette for a dark terminal until the real background color
		// arrives, which it does in the first few messages.
		styles:    NewStyles(true),
		expected:  request.Last,
		streaming: true,
	}

	_, err = tea.NewProgram(model, tea.WithContext(ctx)).Run()
	return err
}

// streamEventMsg is one line of the classify stream.
type streamEventMsg client.StreamEvent

// streamDoneMsg means the server stopped writing.
type streamDoneMsg struct{}

// appliedMsg is the outcome of a write, carrying whatever was written before it
// stopped so a failure part way through does not lose what already happened.
type appliedMsg struct {
	threads []string
	labels  int
	err     error
}

// Init asks the terminal what color it is and starts reading the stream.
func (model RunModel) Init() tea.Cmd {
	return tea.Batch(tea.RequestBackgroundColor, waitForEvent(model.events))
}

// Update folds one message into the screen.
func (model RunModel) Update(message tea.Msg) (tea.Model, tea.Cmd) {
	switch message := message.(type) {
	case tea.BackgroundColorMsg:
		model.styles = NewStyles(message.IsDark())
	case tea.WindowSizeMsg:
		model.width, model.height = message.Width, message.Height
	case tea.KeyPressMsg:
		return model.onKey(message)
	case streamEventMsg:
		return model.onEvent(client.StreamEvent(message))
	case streamDoneMsg:
		model.streaming = false
	case appliedMsg:
		return model.onApplied(message), nil
	}
	return model, nil
}

// waitForEvent takes the next line off the stream, or reports that there are no
// more of them.
func waitForEvent(events <-chan client.StreamEvent) tea.Cmd {
	return func() tea.Msg {
		event, open := <-events
		if !open {
			return streamDoneMsg{}
		}
		return streamEventMsg(event)
	}
}

// onEvent appends a thread, records the totals, or shows what went wrong, and
// then goes back for the next line.
func (model RunModel) onEvent(event client.StreamEvent) (tea.Model, tea.Cmd) {
	switch {
	case event.Err != nil:
		model.failure = event.Err.Error()

	case event.Totals != nil:
		model.totals = event.Totals
		model.expected = event.Totals.Threads

	case event.Result != nil:
		model.rows = append(model.rows, runRow{result: *event.Result, applied: event.Result.Applied})
		model.tokens += event.Result.Usage.InputTokens
		// The expected count starts as a cap rather than a promise, so a run
		// that turns up more threads than that still counts them all.
		model.expected = max(model.expected, len(model.rows))
	}

	return model, waitForEvent(model.events)
}

func (model RunModel) onKey(message tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch {
	case key.Matches(message, model.keys.Quit):
		return model, tea.Quit
	case key.Matches(message, model.keys.Apply):
		return model.startApply()
	}
	return model, nil
}

// pendingApply is one thread's write.
type pendingApply struct {
	threadID string
	labelIDs []string
}

// pending is every thread with labels that have not been written yet.
func (model RunModel) pending() []pendingApply {
	var waiting []pendingApply
	for _, row := range model.rows {
		if row.applied || row.result.Error != "" {
			continue
		}
		assigned := row.result.Assigned()
		if len(assigned) == 0 {
			continue
		}
		ids := make([]string, len(assigned))
		for index, label := range assigned {
			ids[index] = label.ID
		}
		waiting = append(waiting, pendingApply{threadID: row.result.ThreadID, labelIDs: ids})
	}
	return waiting
}

// startApply writes every label currently proposed and not already written.
// Rows that land after this need another press, which is why the key stays live
// once a run has finished.
func (model RunModel) startApply() (tea.Model, tea.Cmd) {
	if model.applying {
		return model, nil
	}

	waiting := model.pending()
	if len(waiting) == 0 {
		model.notice = "Nothing to apply."
		return model, nil
	}

	model.applying = true
	model.notice = ""
	model.failure = ""
	return model, applyAll(model.api, waiting)
}

// applyAll writes each thread in turn, stopping at the first refusal and
// reporting what got through.
func applyAll(api *client.Client, waiting []pendingApply) tea.Cmd {
	return func() tea.Msg {
		var written appliedMsg
		for _, item := range waiting {
			response, err := api.Apply(item.threadID, item.labelIDs)
			if err != nil {
				written.err = err
				return written
			}
			written.threads = append(written.threads, item.threadID)
			written.labels += len(response.Applied)
		}
		return written
	}
}

func (model RunModel) onApplied(message appliedMsg) RunModel {
	model.applying = false

	done := make(map[string]bool, len(message.threads))
	for _, id := range message.threads {
		done[id] = true
	}
	for index := range model.rows {
		if done[model.rows[index].result.ThreadID] {
			model.rows[index].applied = true
		}
	}

	if len(message.threads) > 0 {
		model.notice = fmt.Sprintf("Applied %d %s to %d %s.",
			message.labels, plural(message.labels, "label", "labels"),
			len(message.threads), plural(len(message.threads), "thread", "threads"))
	}
	if message.err != nil {
		model.failure = message.err.Error()
	}

	return model
}

// spent is the token count and what it came to. The server's own figure wins
// once the totals line has arrived.
func (model RunModel) spent() (int, float64) {
	if model.totals != nil {
		return model.totals.InputTokens, model.totals.CostUSD
	}
	return model.tokens, float64(model.tokens) * jevInputTokenCost / 1_000_000
}
