package ui

import (
	"fmt"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/balevine/jev-demo/cli/internal/client"
)

// settingsSample is a form over count labels on a terminal of the given size,
// with a description on the first label and nothing on the rest.
func settingsSample(width, height, count int) SettingsModel {
	labels := make([]client.Label, count)
	for index := range labels {
		labels[index] = client.Label{
			ID:   fmt.Sprintf("Label_%d", index),
			Name: fmt.Sprintf("Label number %d", index),
		}
	}
	labels[0].Description = "receipts, bills, and payment requests"

	model := newSettingsModel(nil, labels)
	model.width, model.height = width, height
	model.resize()
	return model
}

// press sends one key through the screen and hands back what it became.
func press(t *testing.T, model SettingsModel, message tea.KeyPressMsg) SettingsModel {
	t.Helper()

	updated, _ := model.Update(message)
	next, ok := updated.(SettingsModel)
	if !ok {
		t.Fatalf("got %T", updated)
	}
	return next
}

// The screen has to be exactly as tall as the terminal and no line wider than
// it, or the footer scrolls away and the columns stop lining up.
func TestSettingsScreenFitsTheTerminal(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {120, 40}, {60, 12}, {200, 50}} {
		width, height := size[0], size[1]
		screen := settingsSample(width, height, 20).screen()

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

// The marker in the gutter is the only thing saying which row is being edited,
// so exactly one row can carry it.
func TestSettingsMarksOnlyTheActiveRow(t *testing.T) {
	model := settingsSample(120, 24, 5)
	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyDown})

	var marked []string
	for _, line := range strings.Split(model.screen(), "\n") {
		if strings.HasPrefix(line, activeMarker) {
			marked = append(marked, line)
		}
	}

	if len(marked) != 1 {
		t.Fatalf("got %d marked rows: %q", len(marked), marked)
	}
	if !strings.Contains(marked[0], "Label number 1") {
		t.Errorf("the marker is on the wrong row: %q", marked[0])
	}
}

// Tab walks the list and comes back round, so there is no row you can get stuck
// against.
func TestSettingsMovingWrapsAtTheEnds(t *testing.T) {
	model := settingsSample(120, 24, 3)

	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyTab})
	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyTab})
	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyTab})
	if model.active != 0 {
		t.Errorf("tabbing off the end landed on %d, wanted 0", model.active)
	}

	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyTab, Mod: tea.ModShift})
	if model.active != 2 {
		t.Errorf("shift tabbing off the start landed on %d, wanted 2", model.active)
	}
}

// A mailbox has more labels than a short terminal has lines, so the list
// scrolls to keep the row being edited on screen.
func TestSettingsListFollowsTheActiveRow(t *testing.T) {
	model := settingsSample(120, 10, 20)
	room := model.rowsOnScreen()

	for range 8 {
		model = press(t, model, tea.KeyPressMsg{Code: tea.KeyDown})
	}

	if model.top != model.active-room+1 {
		t.Errorf("row %d is on a window starting at %d with room for %d",
			model.active, model.top, room)
	}
	if !strings.Contains(model.screen(), "Label number 8") {
		t.Error("the row being edited scrolled off the screen")
	}

	// Coming back up moves the window no further than it has to.
	for range 8 {
		model = press(t, model, tea.KeyPressMsg{Code: tea.KeyUp})
	}
	if model.top != 0 {
		t.Errorf("got a window starting at %d, wanted the top of the list", model.top)
	}
}

// A save carries a row for every label, including the ones left blank, because
// clearing a field is how a description is taken away.
func TestSettingsWrittenCarriesEveryLabel(t *testing.T) {
	model := settingsSample(120, 24, 4)
	written := model.written()

	if len(written) != 4 {
		t.Fatalf("got %d rows, wanted 4: %+v", len(written), written)
	}
	if written["Label_0"] != "receipts, bills, and payment requests" {
		t.Errorf("got %q", written["Label_0"])
	}
	if written["Label_1"] != "" {
		t.Errorf("got %q, wanted the blank row to travel", written["Label_1"])
	}
}

// Typing lands in the row being edited and nowhere else.
func TestSettingsTypingGoesToTheActiveRow(t *testing.T) {
	model := settingsSample(120, 24, 3)
	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyDown})

	for _, character := range "ops" {
		model = press(t, model, tea.KeyPressMsg{Code: character, Text: string(character)})
	}

	written := model.written()
	if written["Label_1"] != "ops" {
		t.Errorf("got %q", written["Label_1"])
	}
	if written["Label_2"] != "" {
		t.Errorf("got %q, wanted the other rows left alone", written["Label_2"])
	}
}

func TestSettingsSaveKeyStartsASave(t *testing.T) {
	model := settingsSample(120, 24, 3)

	updated, cmd := model.Update(tea.KeyPressMsg{Code: 's', Mod: tea.ModCtrl})
	if cmd == nil {
		t.Fatal("ctrl+s did not start a save")
	}

	next, ok := updated.(SettingsModel)
	if !ok {
		t.Fatalf("got %T", updated)
	}
	if !next.saving {
		t.Error("the screen does not say it is saving")
	}
}

// Saving and walking away are the two ways off this screen, so a save that
// lands closes it and says what was stored.
func TestSettingsSaveClosesTheScreen(t *testing.T) {
	model := settingsSample(120, 24, 3)
	kept := map[string]string{"Label_0": "receipts, bills, and payment requests"}

	updated, cmd := model.onSaved(savedMsg{kept: kept})
	if cmd == nil {
		t.Fatal("a finished save did not close the screen")
	}

	next, ok := updated.(SettingsModel)
	if !ok {
		t.Fatalf("got %T", updated)
	}
	if !next.result.Saved || next.result.Described != 1 || next.result.Labels != 3 {
		t.Errorf("got %+v", next.result)
	}
}

// A save that failed leaves the screen up with the text still in it, so nothing
// typed is lost to a server that was not listening.
func TestSettingsSaveFailureStaysOpen(t *testing.T) {
	model := settingsSample(120, 24, 3)

	updated, cmd := model.onSaved(savedMsg{err: fmt.Errorf("the server answered 500")})
	if cmd != nil {
		t.Error("a failed save closed the screen")
	}

	next, ok := updated.(SettingsModel)
	if !ok {
		t.Fatalf("got %T", updated)
	}
	if next.result.Saved {
		t.Error("a failed save was reported as a save")
	}
	if !strings.Contains(next.screen(), "! the server answered 500") {
		t.Error("the failure is not on the screen")
	}
}

func TestSettingsDiscardQuits(t *testing.T) {
	model := settingsSample(120, 24, 3)
	if _, cmd := model.Update(tea.KeyPressMsg{Code: tea.KeyEscape}); cmd == nil {
		t.Error("esc did not close the screen")
	}
}

// A form with no window size yet has nothing to draw against.
func TestSettingsWaitsForASize(t *testing.T) {
	model := settingsSample(0, 0, 3)
	if got := model.screen(); got != "" {
		t.Errorf("got %q", got)
	}
}

// The count in the footer is what says whether the descriptions actually went
// in, so it follows the fields rather than what was loaded.
func TestSettingsCountFollowsTheFields(t *testing.T) {
	model := settingsSample(120, 24, 4)
	if got := model.countLine(); !strings.Contains(got, "4 labels · 1 described") {
		t.Errorf("got %q", got)
	}

	model = press(t, model, tea.KeyPressMsg{Code: tea.KeyDown})
	model = press(t, model, tea.KeyPressMsg{Code: 'x', Text: "x"})
	if got := model.countLine(); !strings.Contains(got, "4 labels · 2 described") {
		t.Errorf("got %q", got)
	}
}
