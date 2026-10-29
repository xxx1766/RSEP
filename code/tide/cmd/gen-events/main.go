// gen-events regenerates event.pb.go from its complete protobuf descriptor.
// Run at the Tide root: go run ./cmd/gen-events /path/to/protoc-gen-go
package main

import (
	"bytes"
	"fmt"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/pluginpb"
	"os"
	"os/exec"
	"path/filepath"
)

func main() {
	if len(os.Args) != 2 {
		panic("usage: gen-events /path/to/protoc-gen-go")
	}
	body, err := os.ReadFile("proto/eventpb/event.codegen.json")
	must(err)
	req := &pluginpb.CodeGeneratorRequest{}
	must(protojson.Unmarshal(body, req))
	wire, err := proto.Marshal(req)
	must(err)
	cmd := exec.Command(os.Args[1])
	cmd.Stdin = bytes.NewReader(wire)
	cmd.Stderr = os.Stderr
	result, err := cmd.Output()
	must(err)
	resp := &pluginpb.CodeGeneratorResponse{}
	must(proto.Unmarshal(result, resp))
	if resp.GetError() != "" {
		panic(resp.GetError())
	}
	for _, f := range resp.File {
		if f.GetName() != "eventpb/event.pb.go" {
			panic(fmt.Sprintf("unexpected output %q", f.GetName()))
		}
		must(os.WriteFile(filepath.Join("proto", f.GetName()), []byte(f.GetContent()), 0644))
	}
}
func must(err error) {
	if err != nil {
		panic(err)
	}
}
