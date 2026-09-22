// Package ui holds the jev screens.
package ui

import (
	"strings"

	"charm.land/lipgloss/v2"
)

// Amber is the only color in the app. It marks the running token and cost
// totals and the keybinding hints, and nothing else.
//
// There are two shades because no single amber stays readable on both a light
// and a dark terminal. Everything else sets no color at all, so the terminal
// paints its own default and text is legible either way without the app having
// to work out which theme is in use.
const (
	amberOnLight = "#A35A00"
	amberOnDark  = "#FFB000"
)

// Styles is the whole palette. With two colors to work with, the rest of the
// emphasis comes from weight, reverse video, and whitespace.
type Styles struct {
	// Base sets no foreground or background, which is what keeps body text
	// legible in a light terminal and a dark one alike.
	Base lipgloss.Style

	Amber  lipgloss.Style
	Header lipgloss.Style
	Chip   lipgloss.Style
	Faint  lipgloss.Style
	Banner lipgloss.Style
	Error  lipgloss.Style
}

// NewStyles builds the palette for a terminal that either is or is not dark.
func NewStyles(isDark bool) Styles {
	lightDark := lipgloss.LightDark(isDark)
	amber := lightDark(lipgloss.Color(amberOnLight), lipgloss.Color(amberOnDark))

	return Styles{
		Base:   lipgloss.NewStyle(),
		Amber:  lipgloss.NewStyle().Foreground(amber),
		Header: lipgloss.NewStyle().Bold(true).Underline(true),
		Chip:   lipgloss.NewStyle().Reverse(true),
		Faint:  lipgloss.NewStyle().Faint(true),
		Banner: lipgloss.NewStyle().Bold(true),
		Error:  lipgloss.NewStyle().Bold(true),
	}
}

// chipLines renders label names as bracketed blocks in reverse video, wrapped
// over as many lines as it takes. Every label Jev assigned shows up, because
// seeing them is the whole point of the screen.
func (styles Styles) chipLines(names []string, width int) []string {
	if width <= 0 || len(names) == 0 {
		return nil
	}

	var lines []string
	var line strings.Builder
	used := 0

	for _, name := range names {
		// A label named more widely than the terminal is the one thing that
		// cannot be shown whole.
		chip := clip("["+name+"]", width)
		needed := lipgloss.Width(chip)

		if used > 0 && used+1+needed > width {
			lines = append(lines, line.String())
			line.Reset()
			used = 0
		}
		if used > 0 {
			line.WriteString(" ")
			used++
		}

		line.WriteString(styles.Chip.Render(chip))
		used += needed
	}

	return append(lines, line.String())
}
