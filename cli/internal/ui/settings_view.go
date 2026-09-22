package ui

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
)

const (
	// activeMarker and inactiveMarker fill a two column gutter at the left
	// edge. The marker alone says which row is being edited. There is no
	// highlight bar and no color change, because a marker carries it and the
	// color is spent elsewhere.
	activeMarker   = "> "
	inactiveMarker = "  "

	// maxNameColumn caps the label column, so one very long label name cannot
	// squeeze every description down to nothing.
	maxNameColumn = 24
)

// View draws the form. It takes the whole terminal, because the list scrolls
// inside itself and a list that scrolled the shell would lose the footer.
func (model SettingsModel) View() tea.View {
	view := tea.NewView(model.screen())
	view.AltScreen = true
	return view
}

func (model SettingsModel) screen() string {
	// Nothing has told us how big the terminal is yet.
	if model.width == 0 || model.height == 0 {
		return ""
	}

	footer := model.footer()

	lines := make([]string, 0, model.height)
	lines = append(lines, model.header())
	lines = append(lines, model.visibleRows(model.rowsOnScreen())...)

	// The footer is pinned to the bottom, so whatever is left over is blank.
	for len(lines)+len(footer) < model.height {
		lines = append(lines, "")
	}

	return strings.Join(append(lines, footer...), "\n")
}

// rowsOnScreen is what the list gets once the header and the footer have taken
// theirs.
func (model SettingsModel) rowsOnScreen() int {
	return max(model.height-1-len(model.footer()), 0)
}

// nameColumn is how much room the label names need.
func (model SettingsModel) nameColumn() int {
	widest := 0
	for _, label := range model.labels {
		widest = max(widest, lipgloss.Width(label.Name))
	}
	return clamp(widest, minColumn, maxNameColumn)
}

// descriptionColumn is what is left for the text field. One column is held back
// because the field draws a cursor after whatever is typed in it.
func (model SettingsModel) descriptionColumn() int {
	taken := len(inactiveMarker) + model.nameColumn() + len(columnGap)
	return max(model.width-taken-1, minColumn)
}

func (model SettingsModel) header() string {
	return inactiveMarker +
		model.styles.Header.Render(cell("Label", model.nameColumn())) + columnGap +
		model.styles.Header.Render(cell("Description", model.descriptionColumn()))
}

// visibleRows is the window of the list that fits, which follows the row being
// edited.
func (model SettingsModel) visibleRows(room int) []string {
	if room <= 0 {
		return nil
	}

	end := min(model.top+room, len(model.labels))
	lines := make([]string, 0, max(end-model.top, 0))
	for index := model.top; index < end; index++ {
		lines = append(lines, model.settingsRow(index))
	}
	return lines
}

func (model SettingsModel) settingsRow(index int) string {
	marker := inactiveMarker
	if index == model.active {
		marker = activeMarker
	}

	row := marker +
		cell(model.labels[index].Name, model.nameColumn()) + columnGap +
		model.inputs[index].View()

	// The field pads itself out to the room it was given, so a row can come
	// back a column wider than the terminal. Cutting it here is what stops it
	// wrapping and pushing the footer off the bottom.
	return clip(row, model.width)
}

// footer is the blank line and the two or three lines under the list.
func (model SettingsModel) footer() []string {
	lines := []string{""}
	if model.failure != "" {
		lines = append(lines, model.styles.Error.Render(clip("! "+model.failure, model.width)))
	}
	return append(lines, model.countLine(), model.styles.Amber.Render(model.keys.hints()))
}

// countLine says how many labels there are and how many of them say what they
// mean, which is the one number worth watching on this screen.
func (model SettingsModel) countLine() string {
	described := 0
	for index := range model.inputs {
		if strings.TrimSpace(model.inputs[index].Value()) != "" {
			described++
		}
	}

	counted := fmt.Sprintf("%d %s · %d described",
		len(model.labels), plural(len(model.labels), "label", "labels"), described)
	if model.saving {
		counted += " · " + model.styles.Banner.Render("saving")
	}
	return clip(counted, model.width)
}
