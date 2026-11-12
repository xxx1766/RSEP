package stub

import (
	"context"
	"fmt"

	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
)

func SubmitExternal(ctx context.Context, st interfaces.Stub, r *routine.Routine) error {
	s, ok := st.(*stub)
	if !ok || s == nil {
		return fmt.Errorf("external submission requires a native Tide stub")
	}
	if ctx == nil || r == nil {
		return fmt.Errorf("external submission requires a context and routine")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := handleSubmitRtn(s, nil, &interfaces.Event{Routine: r}); err != nil {
		return err
	}
	go waitExternalReceive(ctx, s, r)
	return nil
}

func waitExternalReceive(ctx context.Context, s *stub, r *routine.Routine) {
	select {
	case <-ctx.Done():
		return
	case <-r.WaitReceive():
	}
	if ctx.Err() != nil {
		return
	}
	toCloud, receiver := r.Receiver()
	if !toCloud && receiver == s.id {
		return
	}
	sendRtnWaitEvt(r, s.appId, s.id, true)
}
