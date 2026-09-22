package ui

import (
	"context"
	"errors"

	"charm.land/bubbles/v2/key"
	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"

	"github.com/balevine/jev-demo/cli/internal/client"
)

// SettingsResult is what the screen did before it closed, so whoever opened it
// can say so once the screen is gone.
type SettingsResult struct {
	Saved     bool
	Labels    int
	Described int
}

// SettingsModel is the jev settings screen, one row per label.
//
// A label name on its own is thin. "Ops" could be anything. The description
// written here is what the question about that label actually says, which makes
// this the one screen in the app where somebody puts something in rather than
// reading something out.
type SettingsModel struct {
	api    *client.Client
	keys   settingsKeys
	styles Styles

	width  int
	height int

	labels []client.Label
	inputs []textinput.Model

	// active is the row being edited and top is the first row on screen. A
	// mailbox usually has more labels than a terminal has lines, so the list
	// scrolls under the header.
	active int
	top    int

	saving  bool
	result  SettingsResult
	failure string
}

// Settings opens the label description screen and blocks until whoever is
// watching saves or walks away.
func Settings(ctx context.Context, api *client.Client) (SettingsResult, error) {
	// The labels are fetched before the screen opens, so a server that is not
	// there or a mailbox with nothing to describe prints plainly instead of
	// flashing a TUI.
	labels, err := api.Labels()
	if err != nil {
		return SettingsResult{}, err
	}
	if len(labels) == 0 {
		return SettingsResult{}, errors.New(
			"There are no labels of your own in this mailbox, so there is nothing to describe. Make one in Gmail and run this again.")
	}

	final, err := tea.NewProgram(newSettingsModel(api, labels), tea.WithContext(ctx)).Run()
	if err != nil {
		return SettingsResult{}, err
	}

	model, _ := final.(SettingsModel)
	return model.result, nil
}

func newSettingsModel(api *client.Client, labels []client.Label) SettingsModel {
	model := SettingsModel{
		api:  api,
		keys: newSettingsKeys(),
		// A palette for a dark terminal until the real background color
		// arrives, which it does in the first few messages.
		styles: NewStyles(true),
		labels: labels,
		inputs: make([]textinput.Model, len(labels)),
	}

	for index, label := range labels {
		input := textinput.New()
		// The gutter marker already says which row is being edited, so the
		// field brings no prompt of its own.
		input.Prompt = ""
		input.SetValue(label.Description)
		model.inputs[index] = input
	}

	model.restyle()
	model.inputs[0].Focus()
	return model
}

// savedMsg is the outcome of a save, carrying back the descriptions the server
// kept.
type savedMsg struct {
	kept map[string]string
	err  error
}

// Init asks the terminal what color it is and starts the cursor blinking.
func (model SettingsModel) Init() tea.Cmd {
	return tea.Batch(tea.RequestBackgroundColor, textinput.Blink)
}

// Update folds one message into the screen.
func (model SettingsModel) Update(message tea.Msg) (tea.Model, tea.Cmd) {
	switch message := message.(type) {
	case tea.BackgroundColorMsg:
		model.styles = NewStyles(message.IsDark())
		model.restyle()
		return model, nil
	case tea.WindowSizeMsg:
		model.width, model.height = message.Width, message.Height
		model.resize()
		return model, nil
	case tea.KeyPressMsg:
		return model.onKey(message)
	case savedMsg:
		return model.onSaved(message)
	}

	// Anything else is the cursor asking to blink, which only the row being
	// edited cares about.
	return model, model.updateActive(message)
}

func (model SettingsModel) onKey(message tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch {
	case key.Matches(message, model.keys.Quit), key.Matches(message, model.keys.Discard):
		return model, tea.Quit
	case key.Matches(message, model.keys.Save):
		return model.startSave()
	case key.Matches(message, model.keys.Prev):
		return model, model.move(-1)
	case key.Matches(message, model.keys.Next):
		return model, model.move(1)
	}

	return model, model.updateActive(message)
}

// updateActive hands a message to the row being edited, which is the only one
// that listens.
func (model *SettingsModel) updateActive(message tea.Msg) tea.Cmd {
	if len(model.inputs) == 0 {
		return nil
	}

	var cmd tea.Cmd
	model.inputs[model.active], cmd = model.inputs[model.active].Update(message)
	return cmd
}

// move steps to another row, wrapping at the ends so tab walks the whole list
// and comes back round.
func (model *SettingsModel) move(step int) tea.Cmd {
	if len(model.inputs) == 0 {
		return nil
	}

	model.inputs[model.active].Blur()
	model.active = (model.active + step + len(model.inputs)) % len(model.inputs)

	cmd := model.inputs[model.active].Focus()
	model.inputs[model.active].CursorEnd()
	model.scroll()
	return cmd
}

// scroll moves the window as little as it takes to keep the active row on
// screen, and never past the end of a list shorter than the terminal.
func (model *SettingsModel) scroll() {
	room := model.rowsOnScreen()
	if room <= 0 {
		model.top = model.active
		return
	}

	model.top = clamp(model.top, max(model.active-room+1, 0), model.active)
	model.top = clamp(model.top, 0, max(len(model.labels)-room, 0))
}

// resize gives every description field the room left over once the gutter and
// the label column are taken.
func (model *SettingsModel) resize() {
	width := model.descriptionColumn()
	for index := range model.inputs {
		model.inputs[index].SetWidth(width)
	}
	model.scroll()
}

// restyle hands the palette to the text fields, which otherwise come with
// colors of their own and would match nothing else on screen.
func (model *SettingsModel) restyle() {
	field := textinput.StyleState{
		Text:        model.styles.Base,
		Prompt:      model.styles.Base,
		Placeholder: model.styles.Faint,
		Suggestion:  model.styles.Faint,
	}

	styles := textinput.Styles{
		// Focused and blurred look the same on purpose. The marker in the
		// gutter says which row is being edited, so nothing else has to.
		Focused: field,
		Blurred: field,
		// The cursor is given no color, so it comes out as a block of reverse
		// video in whatever the terminal's own foreground and background are.
		Cursor: textinput.CursorStyle{Shape: tea.CursorBlock, Blink: true},
	}

	for index := range model.inputs {
		model.inputs[index].SetStyles(styles)
	}
}

// startSave writes every description, including the ones left blank. The server
// drops blank text rather than storing it, so clearing a field is how a
// description is taken away.
func (model SettingsModel) startSave() (tea.Model, tea.Cmd) {
	if model.saving {
		return model, nil
	}

	model.saving = true
	model.failure = ""
	return model, saveDescriptions(model.api, model.written())
}

// written is what every row currently says, keyed by label ID.
func (model SettingsModel) written() map[string]string {
	written := make(map[string]string, len(model.labels))
	for index, label := range model.labels {
		written[label.ID] = model.inputs[index].Value()
	}
	return written
}

func saveDescriptions(api *client.Client, written map[string]string) tea.Cmd {
	return func() tea.Msg {
		kept, err := api.SaveDescriptions(written)
		return savedMsg{kept: kept, err: err}
	}
}

// onSaved closes the screen once the descriptions are stored. Saving and
// walking away are the two ways off this screen, and having the save leave too
// keeps them a pair.
func (model SettingsModel) onSaved(message savedMsg) (tea.Model, tea.Cmd) {
	model.saving = false
	if message.err != nil {
		model.failure = message.err.Error()
		return model, nil
	}

	model.result = SettingsResult{
		Saved:     true,
		Labels:    len(model.labels),
		Described: len(message.kept),
	}
	return model, tea.Quit
}
