// revision-worker exposes identical fixed-work kernels through HTTP, PAIX,
// and a one-shot calibration command. No response-time simulation is used.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"runtime"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

var nodeID, tempDir string
var serial sync.Mutex
var workerBoot = time.Now().UnixNano()

type reply struct {
	WorkerInstance string                  `json:"worker_instance"`
	Node           string                  `json:"node"`
	WorkerPID      int                     `json:"worker_pid"`
	WorkerStartNs  int64                   `json:"worker_start_ns"`
	WorkerEndNs    int64                   `json:"worker_end_ns"`
	Result         revisionworkload.Result `json:"result"`
	ServiceNs      int64                   `json:"service_ns"`
	Error          string                  `json:"error,omitempty"`
}

func execute(data []byte) reply {
	start := time.Now()
	var c revisionworkload.Config
	r := reply{Node: nodeID, WorkerPID: os.Getpid(), WorkerStartNs: start.UnixNano(), WorkerInstance: fmt.Sprintf("%s-%d-%d", nodeID, os.Getpid(), workerBoot)}
	if err := json.Unmarshal(data, &c); err != nil {
		r.Error = err.Error()
	} else {
		c.TempDir = tempDir
		var err error
		r.Result, err = revisionworkload.Run(c)
		if err != nil {
			r.Error = err.Error()
		}
	}
	r.ServiceNs = time.Since(start).Nanoseconds()
	r.WorkerEndNs = time.Now().UnixNano()
	return r
}

func handleRun(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method != "POST" {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8192))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	serial.Lock()
	result := execute(data)
	serial.Unlock()
	if result.Error != "" {
		w.WriteHeader(http.StatusBadRequest)
	}
	_ = json.NewEncoder(w).Encode(result)
}

func main() {
	cpuList := flag.String("cpu-list", "", "optional initial CPU affinity for every worker thread")
	mode := flag.String("mode", "paix", "paix, http, or once")
	addr := flag.String("listen", "127.0.0.1:19280", "HTTP listener")
	config := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":1000}`, "one-shot workload JSON")
	flag.StringVar(&nodeID, "node", "unknown", "physical node label")
	flag.StringVar(&tempDir, "tmpdir", "/fig6-io", "shared host-backed I/O directory")
	flag.Parse()
	runtime.GOMAXPROCS(1)
	if err := setCPUs(*cpuList); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(tempDir, 0755); err != nil {
		log.Fatal(err)
	}
	switch *mode {
	case "once":
		r := execute([]byte(*config))
		_ = json.NewEncoder(os.Stdout).Encode(r)
		if r.Error != "" {
			os.Exit(1)
		}
	case "http":
		mux := http.NewServeMux()
		mux.HandleFunc("/run", handleRun)
		mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { fmt.Fprintln(w, "ready") })
		server := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
		log.Fatal(server.ListenAndServe())
	case "paix":
		if err := servePAIX(); err != nil {
			log.Fatal(err)
		}
	default:
		log.Fatalf("unknown mode %q", *mode)
	}
}
