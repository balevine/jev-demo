package cmd

import (
	"context"
	"fmt"
	"io"
	"strings"

	"github.com/spf13/cobra"

	"github.com/balevine/jev-demo/cli/internal/client"
	"github.com/balevine/jev-demo/cli/internal/ui"
)

var (
	runLast      int
	runSince     string
	runBetween   string
	runQuery     string
	runThreshold float64
	runApply     bool
)

var runCmd = &cobra.Command{
	Use:   "run",
	Short: "See what Jev would label your threads",
	Long: `Reads your recent threads, asks Jev about every label you made, and lists what
it would add to each one.

Nothing reaches Gmail until you press a on the list or pass --apply.

Rows appear as the server answers, so the list fills in rather than arriving all
at once.`,
	Args: cobra.NoArgs,
	RunE: runRun,
}

func init() {
	rootCmd.AddCommand(runCmd)

	flags := runCmd.Flags()
	flags.IntVar(&runLast, "last", client.DefaultLast, "How many of your most recent threads to look at")
	flags.StringVar(&runSince, "since", "", "Only threads after this day, as 2026-01-01")
	flags.StringVar(&runBetween, "between", "", "Only threads in this range, as 2026-01-01..2026-02-01")
	flags.StringVar(&runQuery, "query", "", "Extra Gmail search terms, added to whatever the rest selected")
	flags.Float64Var(&runThreshold, "threshold", 0, "How sure Jev has to be before a label counts, from 0 to 1 (default 0.5)")
	flags.BoolVar(&runApply, "apply", false, "Write the labels to Gmail instead of showing the list")
}

func runRun(cmd *cobra.Command, args []string) error {
	api := newClient()
	request := classifyRequest(cmd)

	// Both of these are for something other than a person watching a terminal,
	// so neither opens the list. With --apply the writing is already done by
	// the time a line arrives, and with --json the stream is the whole answer.
	if runApply || jsonOutput {
		return streamPlain(cmd.Context(), cmd.OutOrStdout(), api, request)
	}
	return ui.Run(cmd.Context(), api, request)
}

// classifyRequest turns the flags into what the server expects.
func classifyRequest(cmd *cobra.Command) client.ClassifyRequest {
	request := client.ClassifyRequest{
		Last:    runLast,
		Since:   runSince,
		Between: runBetween,
		Query:   runQuery,
		Apply:   runApply,
	}
	// A threshold of 0 means every label counts, so it only travels when
	// somebody actually asked for it. Otherwise the server picks.
	if cmd.Flags().Changed("threshold") {
		request.Threshold = &runThreshold
	}
	return request
}

// streamPlain writes a run out a line at a time.
func streamPlain(ctx context.Context, out io.Writer, api *client.Client, request client.ClassifyRequest) error {
	events, err := api.Classify(ctx, request)
	if err != nil {
		return err
	}

	// A failure part way through is held onto rather than returned, so the rest
	// of the stream is still read and the server is not left writing into a
	// channel nobody is taking from.
	var failure error
	for event := range events {
		switch {
		case event.Err != nil:
			failure = event.Err
		case jsonOutput:
			fmt.Fprintf(out, "%s\n", event.Raw)
		case event.Totals != nil:
			fmt.Fprintf(out, "%d threads · %d tokens · $%.5f\n",
				event.Totals.Threads, event.Totals.InputTokens, event.Totals.CostUSD)
		case event.Result != nil:
			printResult(out, *event.Result)
		}
	}
	return failure
}

// printResult writes one thread and whatever Jev put on it.
func printResult(out io.Writer, result client.ThreadResult) {
	if result.Error != "" {
		fmt.Fprintf(out, "! %s: %s\n", result.Subject, result.Error)
		return
	}

	assigned := result.Assigned()
	if len(assigned) == 0 {
		fmt.Fprintf(out, "%s: no labels\n", result.Subject)
		return
	}

	names := make([]string, len(assigned))
	for index, label := range assigned {
		names[index] = label.Name
	}
	line := fmt.Sprintf("%s: %s", result.Subject, strings.Join(names, ", "))
	if result.Applied {
		line += " (written)"
	}
	fmt.Fprintln(out, line)
}
