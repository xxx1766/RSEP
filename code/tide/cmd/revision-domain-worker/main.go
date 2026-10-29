package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"runtime"
	"time"

	"github.com/ict/tide/pkg/revisiondomainworkload"
)

var worker *revisiondomainworkload.Worker

func execute(data []byte) revisiondomainworkload.Reply {
	var c revisiondomainworkload.Config
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	err := d.Decode(&c)
	if err == nil {
		var extra any
		if d.Decode(&extra) != io.EOF {
			err = io.ErrUnexpectedEOF
		}
	}
	if err != nil {
		r := worker.Identity()
		r.RequestID = c.RequestID
		r.Error = err.Error()
		return r
	}
	return worker.Run(c)
}

func newMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(405)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(worker.Identity())
	})
	mux.HandleFunc("/run", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(405)
			return
		}
		data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8192))
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		result := execute(data)
		w.Header().Set("Content-Type", "application/json")
		if result.Error != "" {
			w.WriteHeader(400)
		}
		_ = json.NewEncoder(w).Encode(result)
	})
	return mux
}

func main() {
	mode := flag.String("mode", "paix", "http, paix, or once")
	listen := flag.String("listen", "127.0.0.1:34062", "HTTP listener")
	node := flag.String("node", "unknown", "command label; physical placement requires runtime/cgroup evidence")
	dir := flag.String("tmpdir", "/tmp/domain", "durable request journal directory")
	cpus := flag.String("cpu-list", "", "optional worker affinity")
	work := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"once"}`, "one-shot request JSON")
	flag.Parse()
	runtime.GOMAXPROCS(1)
	if err := setCPUs(*cpus); err != nil {
		log.Fatal(err)
	}
	var err error
	worker, err = revisiondomainworkload.New(*node, *dir)
	if err != nil {
		log.Fatal(err)
	}
	defer worker.Close()
	switch *mode {
	case "once":
		r := execute([]byte(*work))
		_ = json.NewEncoder(os.Stdout).Encode(r)
		if r.Error != "" {
			os.Exit(1)
		}
	case "http":
		server := &http.Server{Addr: *listen, Handler: newMux(), ReadHeaderTimeout: 5 * time.Second}
		listener, err := net.Listen("tcp", *listen)
		if err != nil {
			log.Fatal(err)
		}
		if err := worker.Ready(); err != nil {
			listener.Close()
			log.Fatal(err)
		}
		log.Fatal(server.Serve(listener))
	case "paix":
		if err := servePAIX(); err != nil {
			log.Fatal(err)
		}
	default:
		log.Fatalf("unknown mode %q", *mode)
	}
}
