package main

import "testing"

func TestPhaseSummaryKeepsCohortAndCompletionWindowSeparate(t *testing.T) {
	events := []FaultEvent{{Phase: "connected"}, {Phase: "disconnected", AtNS: 100}, {Phase: "recovered", AtNS: 200}}
	rows := []Record{{SubmitNS: 20, ReplyNS: 120, Node: 0, Status: 200, OnTime: true}, {SubmitNS: 130, ReplyNS: 140, Node: 1, Status: 503}, {SubmitNS: 230, ReplyNS: 330, Node: 0, Status: 200, OnTime: true}}
	got := Summarize(rows, events, 300)
	if got[0].Arrivals != 1 || got[0].Successes != 1 || got[0].WindowOnTimeCompletions != 0 || got[1].WindowOnTimeCompletions != 1 || got[1].Failures != 1 || got[2].Successes != 1 || got[2].WindowOnTimeCompletions != 0 || got[2].DrainedAfterWindow != 1 {
		t.Fatalf("summary %+v", got)
	}
}

func TestPhaseSummaryExcludesFinalEndpoint(t *testing.T) {
	events := []FaultEvent{{Phase: "connected"}, {Phase: "disconnected", AtNS: 100}, {Phase: "recovered", AtNS: 200}}
	rows := []Record{{SubmitNS: 230, ReplyNS: 300, Node: 0, Status: 200, OnTime: true}}
	got := Summarize(rows, events, 300)
	if got[2].WindowOnTimeCompletions != 0 || got[2].DrainedAfterWindow != 1 {
		t.Fatalf("endpoint incorrectly belongs to half-open window: %+v", got[2])
	}
}
