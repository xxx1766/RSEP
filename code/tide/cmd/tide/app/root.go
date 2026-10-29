package app

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/ict/tide/cmd/tide/app/serve"
	"github.com/ict/tide/cmd/tide/app/submit"
)

var rootCmd = &cobra.Command{
	Use:   "tide",
	Short: "Tide is a distributed computing framework",
	Long:  `Tide is a distributed computing framework`,
	Run: func(cmd *cobra.Command, args []string) {
		// Do Stuff Here
	},
	Args: cobra.MinimumNArgs(1),
}

func init() {
	cobra.OnInitialize()
	rootCmd.AddCommand(serve.ServeCmd)
	rootCmd.AddCommand(submit.SubmitCmd)
}

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
}
