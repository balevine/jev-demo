package ui

import (
	"strings"

	"charm.land/bubbles/v2/key"
)

// runKeys is everything the run screen listens for. There is no detail view and
// no scrolling, so this is the whole of it.
type runKeys struct {
	Apply key.Binding
	Quit  key.Binding
}

func newRunKeys() runKeys {
	return runKeys{
		Apply: key.NewBinding(key.WithKeys("a"), key.WithHelp("a", "apply all")),
		Quit:  key.NewBinding(key.WithKeys("q", "esc", "ctrl+c"), key.WithHelp("q", "quit")),
	}
}

// hints is the footer line that advertises the keys, which is the only place
// they are written down.
func (keys runKeys) hints() string {
	return hints(keys.Apply, keys.Quit)
}

// settingsKeys is everything the settings screen listens for. Anything not in
// here is typing, and typing goes to the row being edited.
type settingsKeys struct {
	Prev    key.Binding
	Next    key.Binding
	Save    key.Binding
	Discard key.Binding
	Quit    key.Binding
}

func newSettingsKeys() settingsKeys {
	return settingsKeys{
		Prev:    key.NewBinding(key.WithKeys("up", "shift+tab")),
		Next:    key.NewBinding(key.WithKeys("down", "tab")),
		Save:    key.NewBinding(key.WithKeys("ctrl+s"), key.WithHelp("ctrl+s", "save")),
		Discard: key.NewBinding(key.WithKeys("esc"), key.WithHelp("esc", "discard")),
		Quit:    key.NewBinding(key.WithKeys("ctrl+c")),
	}
}

// hints is the footer line. Moving has four keys and one meaning, so it is
// written out here rather than listed key by key.
func (keys settingsKeys) hints() string {
	return "↑/↓ move · tab next field · " + hints(keys.Save, keys.Discard)
}

// hints joins bindings into `a apply all · q quit`.
func hints(bindings ...key.Binding) string {
	written := make([]string, 0, len(bindings))
	for _, binding := range bindings {
		help := binding.Help()
		written = append(written, help.Key+" "+help.Desc)
	}
	return strings.Join(written, " · ")
}
