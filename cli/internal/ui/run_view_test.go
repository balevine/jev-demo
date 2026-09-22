package ui

import (
	"strings"
	"testing"

	"charm.land/lipgloss/v2"
)

// Every column has to take exactly the room it was given, or nothing under the
// header lines up.
func TestCellIsExactlyTheWidthAsked(t *testing.T) {
	for _, text := range []string{"", "short", "a subject long enough to be cut off", "wraps\nover\nlines"} {
		for _, width := range []int{1, 8, 20, 48} {
			if got := lipgloss.Width(cell(text, width)); got != width {
				t.Errorf("cell(%q, %d) is %d wide", text, width, got)
			}
		}
	}
}

func TestClipDoesNotPad(t *testing.T) {
	if got := clip("short", 20); got != "short" {
		t.Errorf("got %q", got)
	}
	if got := lipgloss.Width(clip(strings.Repeat("x", 40), 10)); got != 10 {
		t.Errorf("got %d wide", got)
	}
}

// A snippet arrives with whatever the email had in it, and a row is one line.
func TestClipFlattensLineBreaks(t *testing.T) {
	if got := clip("two\nlines\there", 40); got != "two lines here" {
		t.Errorf("got %q", got)
	}
}

// Every layout has to add up to the terminal width, because a row that runs
// past the edge wraps and a wrapped row puts the whole list out of line. The
// narrowest terminal that can hold five columns at their floor is where this
// starts, since anything under that has nowhere left to take room from.
func TestLayoutFitsTheTerminal(t *testing.T) {
	narrowest := columnCount*minColumn + (columnCount-1)*len(columnGap)
	for width := narrowest; width <= 240; width++ {
		columns := newLayout(width)
		used := columns.subject + columns.date + columns.from + columns.snippet +
			(columnCount-1)*len(columnGap)
		if used != width {
			t.Errorf("a %d column terminal got a %d column layout: %+v", width, used, columns)
		}
	}
}

// Every label Jev assigned has to show up somewhere, however many there are and
// however narrow the terminal is.
func TestChipLinesKeepEveryLabel(t *testing.T) {
	styles := NewStyles(true)
	names := []string{"Invoices", "Receipts", "Ops", "Newsletters", "Recruiting"}

	// 13 is the widest chip in the list, so from there up nothing has to be cut.
	for _, width := range []int{13, 24, 60, 200} {
		lines := styles.chipLines(names, width)
		joined := strings.Join(lines, "\n")
		for _, name := range names {
			if !strings.Contains(joined, name) {
				t.Errorf("at width %d, %q was left off: %q", width, name, joined)
			}
		}
		for _, line := range lines {
			if got := lipgloss.Width(line); got > width {
				t.Errorf("at width %d, a line came out %d wide: %q", width, got, line)
			}
		}
	}
}

// A label named more widely than the terminal is the one thing that cannot be
// shown whole. It is cut rather than left off or allowed to run over.
func TestChipLinesCutALabelWiderThanTheTerminal(t *testing.T) {
	styles := NewStyles(true)
	lines := styles.chipLines([]string{strings.Repeat("x", 40)}, 12)

	if len(lines) != 1 {
		t.Fatalf("got %d lines, wanted 1", len(lines))
	}
	if got := lipgloss.Width(lines[0]); got != 12 {
		t.Errorf("got %d wide, wanted 12", got)
	}
}

// A run of labels wraps rather than running off the edge, and a thread with
// room for all of them stays on one line.
func TestChipLinesWrapOnlyWhenTheyHaveTo(t *testing.T) {
	styles := NewStyles(true)
	names := []string{"Invoices", "Receipts"}

	if lines := styles.chipLines(names, 21); len(lines) != 1 {
		t.Errorf("got %d lines, wanted 1: %q", len(lines), lines)
	}
	if lines := styles.chipLines(names, 20); len(lines) != 2 {
		t.Errorf("got %d lines, wanted 2: %q", len(lines), lines)
	}
	if lines := styles.chipLines(nil, 20); lines != nil {
		t.Errorf("got %q, wanted nothing", lines)
	}
}

func TestShortSenderPrefersTheName(t *testing.T) {
	cases := map[string]string{
		`Sam Reed <sam@example.com>`: "Sam Reed",
		`billing@acme.com`:           "billing@acme.com",
		`not an address at all`:      "not an address at all",
	}
	for header, want := range cases {
		if got := shortSender(header); got != want {
			t.Errorf("shortSender(%q) = %q, wanted %q", header, got, want)
		}
	}
}

func TestShortDateFallsBackToWhatArrived(t *testing.T) {
	if got := shortDate("sometime"); got != "sometime" {
		t.Errorf("got %q", got)
	}
	if got := shortDate("2026-09-18T14:02:11Z"); len(got) != len(displayTimeLayout) {
		t.Errorf("got %q", got)
	}
}

func TestCommas(t *testing.T) {
	cases := map[int]string{0: "0", 812: "812", 1284: "1,284", 1000000: "1,000,000"}
	for value, want := range cases {
		if got := commas(value); got != want {
			t.Errorf("commas(%d) = %q, wanted %q", value, got, want)
		}
	}
}
