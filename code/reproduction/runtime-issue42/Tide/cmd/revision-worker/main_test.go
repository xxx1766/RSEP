package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHTTPExecutesSameKernelAndRejectsInvalidRequests(t *testing.T) {
	for _, tc := range []struct {
		body   string
		status int
	}{
		{`{"kind":"cpu","bytes":4096,"iterations":2}`, 200},
		{`{"kind":"unknown","bytes":4096,"iterations":2}`, 400},
		{`{invalid`, 400},
	} {
		r := httptest.NewRequest("POST", "/run", strings.NewReader(tc.body))
		w := httptest.NewRecorder()
		handleRun(w, r)
		if w.Code != tc.status {
			t.Fatalf("status=%d want%d: %s", w.Code, tc.status, w.Body)
		}
		if tc.status == 200 {
			var got reply
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if got.Result.BytesProcessed != 8192 || got.Result.Checksum == 0 || got.ServiceNs <= 0 {
				t.Fatalf("missing real work/timing: %+v", got)
			}
		}
	}
}
