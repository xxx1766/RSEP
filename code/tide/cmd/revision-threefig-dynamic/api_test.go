package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/jpeg"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func manifest() []nodeInfo {
	return []nodeInfo{
		{Campaign: campaignID, Name: "threefig-nj1", Address: "127.0.0.1:31840", Role: "cloud", Kind: "cloud", CPUs: 4, Stub: "cloud-s", StubMode: "fn", Image: "image", Commands: []string{"worker"}},
		{Campaign: campaignID, Name: "threefig-nj2", Address: "127.0.0.2:31840", Role: "edge", Kind: "origin", CPUs: 2, Stub: "entry-s", StubMode: "entry", Image: "image", Commands: []string{"worker"}},
		{Campaign: campaignID, Name: "threefig-nj3", Address: "127.0.0.3:31840", Role: "edge", Kind: "edge", CPUs: 4, Image: "image", Commands: []string{"worker"}},
	}
}
func jpegBody(t *testing.T) []byte {
	t.Helper()
	var b bytes.Buffer
	if err := jpeg.Encode(&b, image.NewRGBA(image.Rect(0, 0, 2, 2)), nil); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func TestManifestAcceptsColdEdgeAndRejectsWrongSelfOrDuplicate(t *testing.T) {
	peers := manifest()
	self := peers[1]
	if err := validatePeers(self, peers); err != nil {
		t.Fatal(err)
	}
	peers[2].Name = peers[0].Name
	if err := validatePeers(self, peers); err == nil {
		t.Fatal("duplicate node accepted")
	}
	peers = manifest()
	peers[1].Stub = "other-entry"
	if err := validatePeers(self, peers); err == nil {
		t.Fatal("wrong origin accepted")
	}
	peers = manifest()
	peers[2].Stub = "precreated-function"
	if err := validatePeers(self, peers); err == nil {
		t.Fatal("warm function accepted as cold edge")
	}
	peers = manifest()
	peers[2].Commands = []string{"different-worker"}
	if err := validatePeers(self, peers); err == nil {
		t.Fatal("mixed workloads accepted")
	}
}
func TestReadJPEGRejectsOverLimitAndInvalidBeforeSubmission(t *testing.T) {
	body := jpegBody(t)
	got, err := readJPEG(bytes.NewReader(body))
	if err != nil || !bytes.Equal(got, body) {
		t.Fatalf("JPEG: %v", err)
	}
	if _, err := readJPEG(strings.NewReader("not jpeg")); err == nil {
		t.Fatal("invalid bytes accepted")
	}
	if _, err := readJPEG(bytes.NewReader(make([]byte, maxPayloadBytes+1))); err == nil {
		t.Fatal("oversize accepted")
	}
}
func TestDestinationRetainsStoppedMappingAndNeverGuesses(t *testing.T) {
	l := newWitnessLedger()
	l.addStub(stubWitness{StubID: "fn-s", Node: "threefig-nj3", Role: "edge", Mode: "fn", Status: "running"})
	l.stopStub("fn-s", 42)
	got := l.destination(false, "fn-s")
	if !got.Known || got.Node != "threefig-nj3" {
		t.Fatalf("lost lifetime destination %+v", got)
	}
	if l.destination(false, "missing").Known {
		t.Fatal("unknown stub guessed")
	}
	l.setPeers(manifest())
	if got = l.destination(true, "threefig-nj1"); !got.Known || got.Role != "cloud" {
		t.Fatalf("cloud %+v", got)
	}
	if l.destination(true, "threefig-nj3").Known {
		t.Fatal("noncloud accepted as cloud")
	}
}

type fakePending struct {
	done   chan struct{}
	dest   destination
	result nativeResult
}

func (p *fakePending) waitData() <-chan struct{} { return p.done }
func (p *fakePending) receiver() destination     { return p.dest }
func (p *fakePending) outcome() nativeResult     { return p.result }
func TestRunPreservesInputNativeResultAndDestination(t *testing.T) {
	peers := manifest()
	p := &fakePending{done: make(chan struct{}), dest: destination{Known: true, Node: "threefig-nj3", Role: "edge", ReceiverID: "fn-s"}, result: nativeResult{Status: 3, Success: true, Result: []byte("answer")}}
	close(p.done)
	var received []byte
	a := newAPI(peers[1], time.Second, func(_ context.Context, _ string, b []byte) (pendingRequest, error) {
		received = append([]byte(nil), b...)
		return p, nil
	}, nil, nil)
	a.joined.Store(true)
	body := jpegBody(t)
	req := httptest.NewRequest("POST", "/run", bytes.NewReader(body))
	req.Header.Set("X-Request-ID", "client-1")
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
	}
	var result runResponse
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Status != "success" || result.NativeStatus != 3 || result.After.Node != "threefig-nj3" || result.InputBytes != len(body) || result.ClientRequestID != "client-1" || !bytes.Equal(received, body) || !bytes.Equal(result.Result, []byte("answer")) {
		t.Fatalf("bad response %+v", result)
	}
	if result.Witness.OutputValidation != "worker_witness_unverified" {
		t.Fatal("unsupported model validation claim")
	}
}
func TestRunTimeoutKeepsUnknownPlacementAndUnresolvedRequest(t *testing.T) {
	p := &fakePending{done: make(chan struct{})}
	a := newAPI(manifest()[1], time.Millisecond, func(context.Context, string, []byte) (pendingRequest, error) { return p, nil }, nil, nil)
	a.joined.Store(true)
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, httptest.NewRequest("POST", "/run", bytes.NewReader(jpegBody(t))))
	var result runResponse
	json.Unmarshal(w.Body.Bytes(), &result)
	if w.Code != 504 || result.Status != "timeout" || result.After.Known {
		t.Fatalf("timeout misreported %+v", result)
	}
	if a.submitted.Load()-a.completed.Load() != 1 {
		t.Fatal("timeout discarded unresolved backend request")
	}
	close(p.done)
}
func TestReadOnlyEndpointsAndPeerOneShot(t *testing.T) {
	peers := manifest()
	installed := 0
	a := newAPI(peers[1], time.Second, nil, func([]nodeInfo) error { installed++; return nil }, func() stateResponse {
		return stateResponse{Nodes: []nodeCapacity{{Node: "threefig-nj3", Role: "edge", TotalCPUs: 4, RemainingCPUs: 2, ReservedCPUs: 2, Valid: true}}, LocalProcessors: &processorObservation{Node: "threefig-nj2", TotalCPUs: 12, ReservedCPUs: 2, CommittedCPUs: 4, BorrowedCPUs: 2, PhysicalOccupiedCPUs: 4, ObservedNS: 123}}
	})
	h := a.handler()
	for _, path := range []string{"/state", "/config", "/health"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", path, strings.NewReader("{}")))
		if w.Code != 405 {
			t.Fatalf("mutation permitted %s: %d", path, w.Code)
		}
	}
	payload, _ := json.Marshal(peers)
	for i, want := range []int{200, 409} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/peers", bytes.NewReader(payload)))
		if w.Code != want {
			t.Fatalf("attempt%d code%d body%s", i, w.Code, w.Body.String())
		}
	}
	if installed != 1 {
		t.Fatal("duplicate peer registration")
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/state", nil))
	var got stateResponse
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil || got.Nodes[0].ReservedCPUs != 2 || got.LocalProcessors == nil || got.LocalProcessors.BorrowedCPUs != 2 || got.LocalProcessors.CommittedCPUs != 4 {
		t.Fatalf("state json %s %v", w.Body.String(), err)
	}
}
func TestMalformedPeerJSONDoesNotInstall(t *testing.T) {
	installed := false
	a := newAPI(manifest()[1], time.Second, nil, func([]nodeInfo) error { installed = true; return nil }, nil)
	data, _ := json.Marshal(manifest())
	data = append(data, []byte(" {}")...)
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/peers", bytes.NewReader(data)))
	if w.Code != 400 || installed {
		t.Fatal("trailing JSON installed peers")
	}
}

func TestManifestAcceptsFormalTwelveCPUPerNode(t *testing.T) {
	peers := manifest()
	for i := range peers {
		peers[i].CPUs = 12
	}
	for _, self := range peers {
		if err := validatePeers(self, peers); err != nil {
			t.Fatalf("formal manifest rejected by %s: %v", self.Kind, err)
		}
	}
	peers[2].Stub = "warm-function"
	if err := validatePeers(peers[1], peers); err == nil {
		t.Fatal("formal edge accepted precreated function")
	}
}

func TestManifestEnforcesEvenTwoThroughTwelveCPUOnEveryKind(t *testing.T) {
	for _, budget := range []int{-2, 0, 1, 3, 11, 13, 14} {
		for i, p := range manifest() {
			peers := manifest()
			peers[i].CPUs = budget
			self := peers[1]
			if err := validatePeers(self, peers); err == nil {
				t.Errorf("%s accepted invalid CPU budget %d", p.Kind, budget)
			}
		}
	}
	for _, budget := range []int{2, 4, 6, 8, 10, 12} {
		peers := manifest()
		for i := range peers {
			peers[i].CPUs = budget
		}
		if err := validatePeers(peers[1], peers); err != nil {
			t.Errorf("valid even CPU budget %d: %v", budget, err)
		}
	}
}
