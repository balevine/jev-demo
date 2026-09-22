package cmd

import (
	"encoding/json"
	"fmt"
	"io"

	"github.com/balevine/jev-demo/cli/internal/client"
	"github.com/spf13/cobra"
)

var authCmd = &cobra.Command{
	Use:   "auth",
	Short: "Connect Gmail",
	Long: `Connects Gmail, or reports the connection that is already there.

The server owns the token, not this CLI. Consent opens in a browser on whatever
machine the server runs on, and the token goes into that machine's keychain.

Running this again once Gmail is connected just says which mailbox it is.`,
	Args: cobra.NoArgs,
	RunE: runAuth,
}

func init() {
	rootCmd.AddCommand(authCmd)
}

func runAuth(cmd *cobra.Command, args []string) error {
	api := newClient()
	out := cmd.OutOrStdout()

	status, err := api.AuthStatus()
	if err != nil {
		return err
	}

	if !status.Authenticated {
		if !jsonOutput {
			fmt.Fprintln(out, "Gmail is not connected yet.")
			fmt.Fprintln(out, "A browser is opening on Google's consent screen. Approve it there and this finishes on its own.")
		}
		if status, err = api.AuthLogin(); err != nil {
			return err
		}
	}

	return reportAuth(out, status)
}

// reportAuth says which mailbox the server ended up connected to.
func reportAuth(out io.Writer, status client.AuthStatus) error {
	if jsonOutput {
		encoder := json.NewEncoder(out)
		encoder.SetIndent("", "  ")
		return encoder.Encode(status)
	}

	if status.Email == "" {
		_, err := fmt.Fprintln(out, "Gmail is connected.")
		return err
	}
	_, err := fmt.Fprintf(out, "Gmail is connected as %s.\n", status.Email)
	return err
}
