package ui

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/balevine/jev-demo/cli/internal/client"
)

const (
	// columnGap sits between every pair of columns.
	columnGap   = "  "
	columnCount = 4

	// minColumn is the narrowest a column is allowed to get before the screen
	// gives up on fitting and lets the row run past the edge.
	minColumn = 8

	// maxDate is the width of a timestamp, which never changes.
	maxDate = 16

	// labelIndent sets the labels in from the left edge, so a line of them
	// reads as belonging to the thread above it.
	labelIndent = "  "
)

// layout is how many columns each field gets on a terminal of a given width.
//
// Labels are not in here. They go on their own line under the thread, because
// a column would have to cut a long list off and the labels are the thing
// somebody is watching for.
type layout struct {
	subject int
	date    int
	from    int
	snippet int
}

// newLayout splits the terminal across the four columns. The date takes a fixed
// share because a timestamp is always the same length, and everything else is
// proportional so a wide terminal spends its extra room on the subject and the
// snippet rather than on padding.
//
// The snippet gets whatever is left, which is what makes the row add up to the
// width exactly. A row wider than the terminal would wrap, and a wrapped row
// puts every column after it out of line.
func newLayout(width int) layout {
	content := max(width-(columnCount-1)*len(columnGap), columnCount*minColumn)

	// The date is taken first and in full, because half a timestamp tells you
	// nothing and the other columns lose only a character or two to it.
	date := clamp(content-(columnCount-1)*minColumn, minColumn, maxDate)

	rest := content - date
	from := clamp(rest*18/100, minColumn, 28)
	subject := clamp(rest*33/100, minColumn, 48)
	snippet := rest - from - subject

	// On a narrow terminal the shares round their way past what is there and
	// leave the snippet under its floor. The room comes back out of the
	// subject, which is the widest column and the one that can best spare it.
	if short := minColumn - snippet; short > 0 {
		taken := min(short, subject-minColumn)
		subject -= taken
		snippet += taken
	}

	return layout{subject: subject, date: date, from: from, snippet: snippet}
}

func clamp(value, low, high int) int {
	return min(max(value, low), high)
}

// View draws the list over the footer. The screen takes the whole terminal,
// because rows keep arriving and a list that scrolled the shell would lose the
// footer that says what is happening.
func (model RunModel) View() tea.View {
	view := tea.NewView(model.screen())
	view.AltScreen = true
	return view
}

func (model RunModel) screen() string {
	// Nothing has told us how big the terminal is yet.
	if model.width == 0 || model.height == 0 {
		return ""
	}

	columns := newLayout(model.width)
	footer := model.footer()

	lines := make([]string, 0, model.height)
	lines = append(lines, model.header(columns))
	lines = append(lines, model.visibleLines(max(model.height-1-len(footer), 0), columns)...)

	// The footer is pinned to the bottom, so whatever is left over is blank.
	for len(lines)+len(footer) < model.height {
		lines = append(lines, "")
	}

	return strings.Join(append(lines, footer...), "\n")
}

// visibleLines is the tail of the list, because the thread worth looking at is
// always the one that just landed.
//
// Threads come in whole. A thread and its labels belong together, so one that
// will not fit entire is left off rather than shown with its labels cut away.
func (model RunModel) visibleLines(room int, columns layout) []string {
	if room <= 0 || len(model.rows) == 0 {
		return nil
	}

	var blocks [][]string
	used := 0
	for index := len(model.rows) - 1; index >= 0; index-- {
		block := model.block(model.rows[index], columns)
		if used+len(block) > room {
			break
		}
		blocks = append(blocks, block)
		used += len(block)
	}

	// Not even the newest thread fits, which takes a terminal a few lines tall
	// or a thread with a great many labels. It gets the room there is, starting
	// with the row that says which thread it is.
	if len(blocks) == 0 {
		return model.block(model.rows[len(model.rows)-1], columns)[:room]
	}

	lines := make([]string, 0, used)
	for index := len(blocks) - 1; index >= 0; index-- {
		lines = append(lines, blocks[index]...)
	}
	return lines
}

// block is the lines one thread takes up, which is its row and then whatever
// labels Jev put on it.
func (model RunModel) block(row runRow, columns layout) []string {
	lines := []string{model.row(row, columns)}

	for _, line := range model.styles.chipLines(assignedNames(row.result), model.width-len(labelIndent)) {
		lines = append(lines, labelIndent+line)
	}
	return lines
}

func (model RunModel) header(columns layout) string {
	titles := []string{
		cell("Subject", columns.subject),
		cell("Date", columns.date),
		cell("From", columns.from),
		cell("Snippet", columns.snippet),
	}
	for index, title := range titles {
		titles[index] = model.styles.Header.Render(title)
	}
	return strings.Join(titles, columnGap)
}

func (model RunModel) row(row runRow, columns layout) string {
	result := row.result

	// A thread that failed has nothing to show in the other columns, so it
	// gives up its whole row to saying what went wrong.
	if result.Error != "" {
		subject := cell(result.Subject, columns.subject)
		return model.styles.Error.Render(
			clip("! "+subject+columnGap+result.Error, model.width))
	}

	// The snippet is clipped rather than padded, because it is the last column
	// and padding it would only leave spaces hanging off the end of the line.
	return strings.Join([]string{
		cell(result.Subject, columns.subject),
		model.styles.Faint.Render(cell(shortDate(result.Date), columns.date)),
		cell(shortSender(result.Sender), columns.from),
		model.styles.Faint.Render(clip(result.Snippet, columns.snippet)),
	}, columnGap)
}

// assignedNames is the labels that made the threshold.
func assignedNames(result client.ThreadResult) []string {
	assigned := result.Assigned()
	names := make([]string, len(assigned))
	for index, label := range assigned {
		names[index] = label.Name
	}
	return names
}

// footer is the blank line and the two or three lines under the list.
func (model RunModel) footer() []string {
	lines := []string{""}
	if model.failure != "" {
		lines = append(lines, model.styles.Error.Render(clip("! "+model.failure, model.width)))
	}
	return append(lines, model.progressLine(), model.spentLine())
}

// progressLine says how far along the run is and whether anything has been
// written to Gmail. Both are base text, because color is spent elsewhere.
//
// The count it is measured against starts as the cap the run asked for and is
// replaced by the real number once the totals arrive, so the trailing ellipsis
// is what tells you a run is still going rather than finished early.
func (model RunModel) progressLine() string {
	counted := fmt.Sprintf("%d/%d classified", len(model.rows), max(model.expected, len(model.rows)))
	if model.streaming {
		counted += "…"
	}
	return counted + " · " + model.styles.Banner.Render(model.banner())
}

func (model RunModel) banner() string {
	switch {
	case model.applying:
		return "writing to Gmail"
	case model.notice != "":
		return model.notice
	default:
		return "DRY RUN, nothing written to Gmail"
	}
}

// spentLine is the one amber line, carrying what the run has cost and the keys.
func (model RunModel) spentLine() string {
	tokens, cost := model.spent()
	left := fmt.Sprintf("%s %s · $%.5f", commas(tokens), plural(tokens, "token", "tokens"), cost)
	right := model.keys.hints()

	gap := max(model.width-lipgloss.Width(left)-lipgloss.Width(right), 2)
	return model.styles.Amber.Render(left + strings.Repeat(" ", gap) + right)
}
