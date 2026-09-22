package ui

import (
	"net/mail"
	"strconv"
	"strings"
	"time"

	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

// The server sends timestamps as UTC in ISO 8601. They are shown in local time,
// because a person reading a mailbox thinks in the clock on their wall.
const (
	serverTimeLayout  = time.RFC3339
	displayTimeLayout = "2006-01-02 15:04"
)

// clip cuts text down to at most width display cells, marking the cut with an
// ellipsis. Line breaks and tabs become spaces first, because a snippet arrives
// with whatever the email had in it and a row is one line. Width is counted in
// display cells rather than bytes, so wide characters still line up.
func clip(text string, width int) string {
	if width <= 0 {
		return ""
	}

	text = strings.Map(func(character rune) rune {
		if character == '\n' || character == '\r' || character == '\t' {
			return ' '
		}
		return character
	}, text)

	if lipgloss.Width(text) <= width {
		return text
	}
	return ansi.Truncate(text, width, "…")
}

// cell is clip plus padding, so a column takes exactly the room it was given
// whatever is in it.
func cell(text string, width int) string {
	text = clip(text, width)
	return text + strings.Repeat(" ", max(width-lipgloss.Width(text), 0))
}

// shortDate turns a server timestamp into something a person reads. Anything
// that will not parse is shown exactly as it arrived.
func shortDate(value string) string {
	at, err := time.Parse(serverTimeLayout, value)
	if err != nil {
		return value
	}
	return at.Local().Format(displayTimeLayout)
}

// shortSender pulls the display name out of a From header, falling back to the
// address when the header carries no name and to the raw header when it will
// not parse at all.
func shortSender(value string) string {
	address, err := mail.ParseAddress(value)
	if err != nil {
		return value
	}
	if address.Name != "" {
		return address.Name
	}
	return address.Address
}

// commas writes a number the way a person reads it, as 1,284 rather than 1284.
// Token counts are never negative, so no sign is handled.
func commas(value int) string {
	digits := strconv.Itoa(value)
	var out strings.Builder
	for index, digit := range digits {
		if index > 0 && (len(digits)-index)%3 == 0 {
			out.WriteByte(',')
		}
		out.WriteRune(digit)
	}
	return out.String()
}

// plural picks the right word for a count.
func plural(count int, one, many string) string {
	if count == 1 {
		return one
	}
	return many
}
