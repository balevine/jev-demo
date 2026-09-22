package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/balevine/jev-demo/cli/internal/client"
)

func noul(value float64) *float64 { return &value }

// sample is a screen part way through a run, with a thread that got labels, a
// thread that got none, and a thread that failed.
func sample(width, height int) RunModel {
	model := RunModel{
		keys:      newRunKeys(),
		styles:    NewStyles(true),
		width:     width,
		height:    height,
		expected:  25,
		tokens:    1284,
		streaming: true,
	}

	results := []client.ThreadResult{
		{
			ThreadID: "t1",
			Subject:  "Invoice #4021 for September",
			Sender:   `Acme Billing <billing@acme.com>`,
			Date:     "2026-09-18T14:02:11Z",
			Snippet:  "Your September invoice is attached and due on the first.",
			Labels: []client.LabelScore{
				{ID: "Label_12", Name: "Invoices", Noul: noul(0.93), Apply: true},
				{ID: "Label_14", Name: "Receipts", Noul: noul(0.71), Apply: true},
				{ID: "Label_19", Name: "Recruiting", Noul: noul(0.02)},
			},
		},
		{
			ThreadID: "t2",
			Subject:  "Coffee Thursday?",
			Sender:   "sam@example.com",
			Date:     "2026-09-19T09:00:00Z",
			Snippet:  "Free any time after ten if you are around.",
		},
		{
			ThreadID: "t3",
			Subject:  "Quarterly review",
			Date:     "2026-09-20T08:30:00Z",
			Error:    "Jev answered 429 and would not settle after two retries.",
		},
	}
	for _, result := range results {
		model.rows = append(model.rows, runRow{result: result})
	}
	return model
}

// The screen has to be exactly as tall as the terminal and no line wider than
// it, or the footer scrolls away and the columns stop lining up.
func TestScreenFitsTheTerminal(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {120, 40}, {60, 12}, {200, 50}} {
		width, height := size[0], size[1]
		screen := sample(width, height).screen()

		lines := strings.Split(screen, "\n")
		if len(lines) != height {
			t.Errorf("a %dx%d terminal got %d lines", width, height, len(lines))
		}
		for index, line := range lines {
			if got := lipgloss.Width(line); got > width {
				t.Errorf("a %dx%d terminal got a %d wide line at %d: %q", width, height, got, index, line)
			}
		}
	}
}

// Rows keep arriving, so the list follows the end of itself rather than the
// start. A terminal with room for two rows shows the last two.
func TestScreenFollowsTheNewestRows(t *testing.T) {
	// One line of header, three of footer, and two rows left over.
	screen := sample(120, 6).screen()

	if strings.Contains(screen, "Invoice #4021") {
		t.Error("the oldest row is still on screen")
	}
	if !strings.Contains(screen, "Quarterly review") {
		t.Error("the newest row is missing")
	}
}

// Nothing reaches Gmail until somebody asks, and the footer has to say so.
func TestDryRunIsTheDefaultBanner(t *testing.T) {
	if screen := sample(120, 24).screen(); !strings.Contains(screen, "DRY RUN") {
		t.Error("the dry run banner is missing")
	}
}

// A run with no window size yet has nothing to draw against.
func TestScreenWaitsForASize(t *testing.T) {
	model := RunModel{keys: newRunKeys(), styles: NewStyles(true)}
	if got := model.screen(); got != "" {
		t.Errorf("got %q", got)
	}
}

// Applying writes every thread with labels that have not been written, and
// leaves alone the ones with nothing to add or an error on them.
func TestPendingSkipsThreadsWithNothingToWrite(t *testing.T) {
	model := sample(120, 24)
	waiting := model.pending()

	if len(waiting) != 1 {
		t.Fatalf("got %d threads to write, wanted 1: %+v", len(waiting), waiting)
	}
	if waiting[0].threadID != "t1" {
		t.Errorf("got %q", waiting[0].threadID)
	}
	if len(waiting[0].labelIDs) != 2 {
		t.Errorf("got %v, wanted only the labels that made the threshold", waiting[0].labelIDs)
	}
}

// A thread already written is not written again, so pressing apply twice is a
// no-op rather than a second round of requests.
func TestPendingSkipsWhatWasAlreadyWritten(t *testing.T) {
	model := sample(120, 24)
	model = model.onApplied(appliedMsg{threads: []string{"t1"}, labels: 2})

	if waiting := model.pending(); len(waiting) != 0 {
		t.Errorf("got %+v, wanted nothing left", waiting)
	}
	if !strings.Contains(model.notice, "Applied 2 labels to 1 thread.") {
		t.Errorf("got %q", model.notice)
	}
}

// The totals the server sends win over the running count, because the server is
// the one that knows what a run actually cost.
func TestTotalsReplaceTheRunningCount(t *testing.T) {
	model := sample(120, 24)
	model.totals = &client.RunTotals{Threads: 3, InputTokens: 2000, CostUSD: 0.000084}

	tokens, cost := model.spent()
	if tokens != 2000 || cost != 0.000084 {
		t.Errorf("got %d tokens and $%v", tokens, cost)
	}
}

// A line of the stream turns into a row, and its tokens into the running total.
func TestStreamEventBecomesARow(t *testing.T) {
	model := RunModel{keys: newRunKeys(), styles: NewStyles(true), width: 120, height: 24}
	result := client.ThreadResult{ThreadID: "t1", Subject: "Hello", Usage: client.Usage{InputTokens: 400}}

	updated, cmd := model.Update(streamEventMsg(client.StreamEvent{Result: &result}))
	if cmd == nil {
		t.Error("the stream was not read again")
	}

	next, ok := updated.(RunModel)
	if !ok {
		t.Fatalf("got %T", updated)
	}
	if len(next.rows) != 1 || next.tokens != 400 {
		t.Errorf("got %d rows and %d tokens", len(next.rows), next.tokens)
	}
}

func TestQuitKeyQuits(t *testing.T) {
	model := RunModel{keys: newRunKeys(), styles: NewStyles(true), width: 120, height: 24}
	if _, cmd := model.Update(tea.KeyPressMsg{Code: 'q', Text: "q"}); cmd == nil {
		t.Error("q did not quit")
	}
}
