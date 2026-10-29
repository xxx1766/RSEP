package interfaces

import "fmt"

// Cluster State
const (
	NodeNewEvt  = "/clsstat/new_node"
	NodeSyncEvt = "/clsstat/sync_node"

	StubNewEvt  = "/clsstat/new_stub"
	StubSyncEvt = "/clsstat/sync_stub"

	AppNewEvt = "/clsstat/new_app"
)

// Stub
const (
	rtnSbmEvtFmt  = "/stub/%s/submit_routine"
	rtnRecvEvtFmt = "/stub/%s/routine_receive"

	rtnWaitEvtFmt = "/stub/%s/routine_wait"
	rtnDoneEvtFmt = "/stub/%s/routine_done"
)

func RtnSbmEvt(sid string) string {
	return fmt.Sprintf(rtnSbmEvtFmt, sid)
}

func RtnRecvEvt(sid string) string {
	return fmt.Sprintf(rtnRecvEvtFmt, sid)
}

func RtnWaitEvt(sid string) string {
	return fmt.Sprintf(rtnWaitEvtFmt, sid)
}

func RtnDoneEvt(sid string) string {
	return fmt.Sprintf(rtnDoneEvtFmt, sid)
}

// Stub Manager
const (
	StubCreateEvt = "/stubmgr/create_stub"
	StubStopEvt   = "/stubmgr/stop_stub"
	AppStopEvt    = "/stubmgr/stop_app"
)

// Cloud
const (
	cldRtnSbmEvtFmt  = "/cloud/app/%s/submit_rtn"
	cldRtnWaitEvtFmt = "/cloud/app/%s/wait_routine"
)

func CldRtnSbmEvt(aid string) string {
	return fmt.Sprintf(cldRtnSbmEvtFmt, aid)
}

func CldRtnWaitEvt(aid string) string {
	return fmt.Sprintf(cldRtnWaitEvtFmt, aid)
}
