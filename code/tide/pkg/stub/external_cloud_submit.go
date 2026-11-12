package stub

import (
	"context"
	"fmt"

	"github.com/ict/tide/pkg/c4"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"google.golang.org/protobuf/proto"
)

func SubmitExternalCloud(ctx context.Context, st interfaces.Stub, r *routine.Routine, cloudNodeID string) error {
	s, ok := st.(*stub)
	if !ok || s == nil {
		return fmt.Errorf("cloud submission requires a native Tide source stub")
	}
	if ctx == nil || r == nil {
		return fmt.Errorf("cloud submission requires context and routine")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	cs := clsstat.Singleton()
	cloud := cs.GetNode(cloudNodeID)
	if cloud == nil || cloud.Role != clsstat.CLOUD {
		return fmt.Errorf("destination %q is not a known cloud node", cloudNodeID)
	}
	self := cs.GetSelf()
	if self == nil {
		return fmt.Errorf("local source node missing")
	}
	r.InitSid = s.id
	r.RequireLocalCapacity = r.RequireLocalCapacity || dev.CurrentConfig().C4Functions[r.FnName]
	if r.RequireLocalCapacity && r.LocalNodeID == "" {
		r.LocalNodeID = self.Id
	}
	if err := c4.Check(r); err != nil {
		return err
	}
	content, err := proto.Marshal(routine.SubmitEvent(r))
	if err != nil {
		return err
	}
	s.rtnStore.Record(r)
	if err = cmnct.Singleton().SendToNode(cloudNodeID, &event.Event{Type: interfaces.CldRtnSbmEvt(s.appId), Content: content}); err != nil {
		return err
	}
	r.SetReceiver(true, cloudNodeID)
	go waitExternalReceive(ctx, s, r)
	return nil
}
