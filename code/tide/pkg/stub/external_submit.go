package stub

import (
	"context"
	"fmt"

	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
)

// SubmitExternal admits an external caller through the native submit event
// handler and requests remote result delivery through the native wait protocol.
// The caller waits on r.WaitData(); no executor or executor waiter is fabricated.
// Cancellation stops waiting for a receiver, not already admitted execution.
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
	// Record the origin's routine before the scheduler can send it elsewhere.
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
		// Local execution updates this exact routine. A cloud-local stub may
		// intentionally be absent from the edge routing inventory.
		return
	}
	// WaitReceive is already closed, so the existing protocol's own receiver
	// wait cannot strand this goroutine after the caller has canceled.
	sendRtnWaitEvt(r, s.appId, s.id, true)
}
