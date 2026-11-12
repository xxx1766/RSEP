package submit

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"github.com/spf13/cobra"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
)

var SubmitCmd = &cobra.Command{
	Use:   "submit",
	Short: "submit job to tide system",
	Long:  `submit certain job to tide system`,
	Run: func(cmd *cobra.Command, args []string) {
		ctlRun()
	},
}

var (
	server  string
	name    string
	image   string
	volume  string
	command string
)

func init() {
	SubmitCmd.Flags().StringVarP(&server, "server", "s", "127.0.0.1:10001", "Tide server address")
	SubmitCmd.Flags().StringVarP(&name, "name", "n", "", "Assign a name to the application")
	SubmitCmd.Flags().StringVarP(&image, "image", "i", "", "Specify the application image")
	SubmitCmd.Flags().StringVarP(&volume, "volume", "v", "", "Specify the volume pair")
	SubmitCmd.Flags().StringVarP(&command, "command", "c", "", "Specify the command to run")
}

func ctlRun() error {

	if name == "" {
		name = stringx.GenerateId()
	}

	commands := strings.Fields(command)

	stCrtEvt := eventpb.StubCreateEvent{
		AppId:      name,
		ImageName:  image,
		VolumePair: volume,
		Commands:   commands,
	}
	content, err := proto.Marshal(&stCrtEvt)
	if err != nil {
		logrus.Debug("Run: failed to marshal StubCreateEvent, ", err)
		return err
	}
	evtPb := &eventpb.Event{
		Type:    interfaces.StubCreateEvt,
		From:    "",
		Content: content,
	}
	evtsPb := &eventpb.EventList{
		Events: []*eventpb.Event{evtPb},
	}

	conn, err := grpc.Dial(server, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		logrus.Debug("Run: failed to connect server, ", err)
		return err
	}
	defer conn.Close()
	cli := eventpb.NewEventServiceClient(conn)

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = cli.SendEvent(ctx, evtsPb)
	if err != nil {
		return err
	}
	fmt.Printf("%s\n", name)
	return nil
}
