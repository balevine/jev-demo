package cmd

import (
	"encoding/json"
	"fmt"
	"io"

	"github.com/spf13/cobra"

	"github.com/balevine/jev-demo/cli/internal/client"
	"github.com/balevine/jev-demo/cli/internal/ui"
)

var settingsCmd = &cobra.Command{
	Use:   "settings",
	Short: "Say what your labels mean",
	Long: `Writes a description for each label you made in Gmail.

A label name on its own is thin. "Ops" could be anything. The description is the
sentence that goes into the question Jev is asked about that label, so this is
where you steer what a label actually catches.

Descriptions are kept by label ID, so renaming a label in Gmail keeps what you
wrote for it. Clearing a field takes the description away.

With --json this prints the labels and their descriptions instead of opening the
form.`,
	Args: cobra.NoArgs,
	RunE: runSettings,
}

func init() {
	rootCmd.AddCommand(settingsCmd)
}

func runSettings(cmd *cobra.Command, args []string) error {
	api := newClient()

	if jsonOutput {
		return printLabels(cmd.OutOrStdout(), api)
	}

	result, err := ui.Settings(cmd.Context(), api)
	if err != nil {
		return err
	}

	// Nothing is printed when somebody walks away, because nothing happened.
	if result.Saved {
		fmt.Fprintf(cmd.OutOrStdout(), "Saved. %d of %d labels have a description.\n",
			result.Described, result.Labels)
	}
	return nil
}

// printLabels writes the labels and whatever is stored for them as JSON.
func printLabels(out io.Writer, api *client.Client) error {
	labels, err := api.Labels()
	if err != nil {
		return err
	}

	encoder := json.NewEncoder(out)
	encoder.SetIndent("", "  ")
	return encoder.Encode(labels)
}
