package stub

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/routine"
)

// These adapters are only linked into the experiment test binary. They retain
// native Stub scheduling/receipt/result paths and use the same verified image
// service as the Kubernetes baseline through a local HTTP executor adapter.
var issue12ImagePayloads [][]byte
var issue12ImageClient = &http.Client{Timeout: 10 * time.Second}

func issue12LoadImagePayloads() error {
	dir := os.Getenv("TMC_ISSUE12_IMAGE_DIR")
	if dir == "" {
		return nil
	}
	issue12ImagePayloads = nil
	for i := 0; i < 4; i++ {
		body, err := os.ReadFile(filepath.Join(dir, fmt.Sprintf("image-%d.jpg", i)))
		if err != nil || len(body) == 0 || len(body) > 1<<20 {
			return fmt.Errorf("image %d load failed: %v (bytes=%d)", i, err, len(body))
		}
		issue12ImagePayloads = append(issue12ImagePayloads, body)
	}
	return nil
}

func issue12ExecuteImage(endpoint string, rtn *routine.Routine) (revisionworkload.Result, error) {
	var result revisionworkload.Result
	digest := sha256.Sum256(rtn.Args)
	checksum := binary.LittleEndian.Uint64(digest[:8])
	req, err := http.NewRequest("POST", endpoint+"/infer", bytes.NewReader(rtn.Args))
	if err != nil {
		return result, err
	}
	req.Header.Set("Content-Type", "image/jpeg")
	req.Header.Set("X-Request-ID", rtn.Id)
	response, err := issue12ImageClient.Do(req)
	if err != nil {
		return result, err
	}
	defer response.Body.Close()
	var row struct {
		Status    string `json:"status"`
		InputSHA  string `json:"input_sha256"`
		OutputSHA string `json:"output_sha256"`
		Checksum  string `json:"checksum"`
		Shape     []int  `json:"shape"`
		Finite    bool   `json:"finite"`
		Error     string `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&row); err != nil {
		return result, err
	}
	if response.StatusCode != 200 || row.Status != "success" || !row.Finite ||
		len(row.Shape) != 3 || row.Shape[0] != 1 || row.Shape[1] != 3549 || row.Shape[2] != 85 ||
		row.InputSHA != fmt.Sprintf("%x", digest) || len(row.OutputSHA) != 64 || row.Checksum != fmt.Sprint(checksum) {
		return result, fmt.Errorf("image inference response verification failed: HTTP %d %+v", response.StatusCode, row)
	}
	return revisionworkload.Result{Checksum: checksum, BytesProcessed: int64(len(rtn.Args))}, nil
}
