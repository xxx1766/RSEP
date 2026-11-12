package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

type reply struct {
	Result    revisionworkload.Result `json:"result"`
	ServiceNs int64                   `json:"service_ns"`
	Error     string                  `json:"error,omitempty"`
}

func execute(data []byte) reply {
	start := time.Now()
	var c revisionworkload.Config
	var r reply
	if err := json.Unmarshal(data, &c); err != nil {
		r.Error = err.Error()
	} else {
		var err error
		r.Result, err = revisionworkload.Run(c)
		if err != nil {
			r.Error = err.Error()
		}
	}
	r.ServiceNs = time.Since(start).Nanoseconds()
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
	result := execute(data)
	if result.Error != "" {
		w.WriteHeader(http.StatusBadRequest)
	}
	_ = json.NewEncoder(w).Encode(result)
}

func main() {
	mode := flag.String("mode", "paix", "paix, http, or once")
	addr := flag.String("listen", "127.0.0.1:19280", "HTTP listener")
	config := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":1000}`, "one-shot workload JSON")
	flag.Parse()
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
