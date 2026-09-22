// Package cmd holds the jev commands.
package cmd

import (
	"fmt"
	"os"

	"github.com/balevine/jev-demo/cli/internal/client"
	"github.com/spf13/cobra"
)

var (
	serverURL  string
	jsonOutput bool
)

var rootCmd = &cobra.Command{
	Use:   "jev",
	Short: "Label Gmail threads with Jev",
	Long: `jev labels Gmail threads using Jev, TypeSafe's System One model.

Every label you made in Gmail becomes one question, "should this label be added
to this thread?", and all of them are asked in a single request per thread.
Nothing is written to Gmail until you ask for it.

The server does the work. This is a thin client over it, so start the server
first and leave it running.

Get started:
  jev auth      Connect Gmail
  jev settings  Say what your labels mean
  jev run       See what Jev would label your threads`,
	SilenceUsage:  true,
	SilenceErrors: true,
}

// Execute runs the root command.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "! %s\n", err)
		os.Exit(1)
	}
}

func init() {
	flags := rootCmd.PersistentFlags()
	flags.StringVar(&serverURL, "server", client.DefaultServer, "Address of the jev-demo server")
	flags.BoolVar(&jsonOutput, "json", false, "Print JSON instead of prose")
}

// newClient builds a client pointed at whatever --server says.
func newClient() *client.Client {
	return client.New(serverURL)
}
