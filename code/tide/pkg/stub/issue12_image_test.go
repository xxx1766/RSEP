package stub

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/ict/tide/pkg/routine"
)

func TestIssue12ImageExecutorCarriesActualRoutineBytes(t *testing.T) {
	payload := []byte("test JPEG payload; inference itself is verified by the deployed image service")
	digest := sha256.Sum256(payload)
	want := binary.LittleEndian.Uint64(digest[:8])
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if string(body) != string(payload) || r.Header.Get("X-Request-ID") != "image-test" {
			t.Errorf("actual routine bytes or request identity lost")
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "success", "finite": true,
			"shape": []int{1, 3549, 85}, "input_sha256": fmt.Sprintf("%x", digest),
			"output_sha256": fmt.Sprintf("%x", digest), "checksum": fmt.Sprint(want)})
	}))
	defer server.Close()
	got, err := issue12ExecuteImage(server.URL, &routine.Routine{Id: "image-test", Args: payload})
	if err != nil || got.Checksum != want || got.BytesProcessed != int64(len(payload)) {
		t.Fatalf("image result=%+v err=%v", got, err)
	}
}

func TestIssue12ImageExecutorRejectsWrongInputOrInvalidOutput(t *testing.T) {
	for _, body := range []string{
		`{"status":"success","finite":true,"shape":[1,3549,85],"input_sha256":"wrong"}`,
		`{"status":"success","finite":false,"shape":[1,3549,85]}`,
		`{"status":"success","finite":true,"shape":[1,2]}`,
		`{"status":"error","error":"inference failed"}`,
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_, _ = w.Write([]byte(body))
		}))
		_, err := issue12ExecuteImage(server.URL, &routine.Routine{Id: "bad", Args: []byte("JPEG")})
		server.Close()
		if err == nil {
			t.Fatalf("invalid image response was accepted: %s", body)
		}
	}
}
