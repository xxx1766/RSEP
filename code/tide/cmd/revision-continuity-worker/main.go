// revision-continuity-worker exposes real fixed-count CPU work over HTTP/PAIX.
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
	"time"

	"github.com/ict/tide/pkg/revisioncontinuity"
)

var worker *revisioncontinuity.Runner

func execute(data []byte) revisioncontinuity.Reply {
	var config revisioncontinuity.Config
	if err := json.Unmarshal(data, &config); err != nil {
		reply := worker.Identity()
		now := time.Now().UnixNano()
		reply.WorkerStartNs = now
		reply.WorkerEndNs = now
		reply.Error = err.Error()
		return reply
	}
	return worker.Run(config)
}

func handleRun(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8192))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	result := execute(data)
	if result.Error != "" {
		w.WriteHeader(http.StatusBadRequest)
	}
	_ = json.NewEncoder(w).Encode(result)
}

func newMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/run", handleRun)
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(worker.Identity())
	})
	mux.HandleFunc("/progress", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		data, err := worker.Progress(r.URL.Query().Get("run_id"))
		if err != nil {
			status := http.StatusBadRequest
			if os.IsNotExist(err) {
				status = http.StatusNotFound
			}
			http.Error(w, err.Error(), status)
			return
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write(data)
	})
	return mux
}

func main() {
	cpuList := flag.String("cpu-list", "", "optional initial CPU affinity for every worker thread")
	mode := flag.String("mode", "paix", "paix, http, or once")
	addr := flag.String("listen", "127.0.0.1:19280", "HTTP listener")
	config := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":100}`, "one-shot workload JSON")
	node := flag.String("node", "unknown", "physical node label")
	dir := flag.String("tmpdir", "/continuity-progress", "host-backed progress directory")
	flag.Parse()
	runtime.GOMAXPROCS(1)
	if err := setCPUs(*cpuList); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(*dir, 0755); err != nil {
		log.Fatal(err)
	}
	worker = revisioncontinuity.NewRunner(*node, *dir)
	switch *mode {
	case "once":
		result := execute([]byte(*config))
		_ = json.NewEncoder(os.Stdout).Encode(result)
		if result.Error != "" {
			os.Exit(1)
		}
	case "http":
		server := &http.Server{Addr: *addr, Handler: newMux(), ReadHeaderTimeout: 5 * time.Second}
		log.Fatal(server.ListenAndServe())
	case "paix":
		if err := servePAIX(); err != nil {
			log.Fatal(err)
		}
	default:
		log.Fatal(fmt.Sprintf("unknown mode %q", *mode))
	}
}
