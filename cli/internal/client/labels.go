package client

import (
	"context"
	"net/http"
)

// Label is one label the user made in Gmail, with whatever description is
// stored for it. System labels such as INBOX never appear here, because the app
// only ever asks about labels somebody made on purpose.
type Label struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
}

// Labels is every label the user made, sorted by name, each carrying the
// description saved for it.
func (client *Client) Labels() ([]Label, error) {
	ctx, cancel := context.WithTimeout(context.Background(), RequestTimeout)
	defer cancel()

	var labels []Label
	err := client.do(ctx, http.MethodGet, "/labels", nil, &labels)
	return labels, err
}

// SaveDescriptions replaces every stored description with the ones given, and
// returns the ones that were kept. The server drops blank text rather than
// storing it, so what comes back is not always what went in.
func (client *Client) SaveDescriptions(written map[string]string) (map[string]string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), RequestTimeout)
	defer cancel()

	// A nil map encodes as null, which is not an object and not something the
	// server will take.
	if written == nil {
		written = map[string]string{}
	}

	var kept map[string]string
	err := client.do(ctx, http.MethodPut, "/label-descriptions", written, &kept)
	return kept, err
}
