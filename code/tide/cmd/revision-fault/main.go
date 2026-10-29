package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

func main() {
	mode := flag.String("mode", "controller", "controller or worker")
	listen := flag.String("listen", "127.0.0.1:19401", "worker listener")
	workerLog := flag.String("worker-log", "worker.jsonl", "actual worker completion log")
	local := flag.String("local-url", "http://127.0.0.1:19401", "local worker URL")
	remote := flag.String("remote-url", "http://127.0.0.1:19402", "remote worker URL behind owned gate")
	eligible := flag.Bool("local-eligible", true, "allow deadline-feasible local admission")
	phase := flag.Duration("phase", 3*time.Second, "each connected/disconnected/recovered phase duration")
	timeout := flag.Duration("timeout", 2*time.Second, "external response wait including FIFO queue")
	qos := flag.Duration("qos", 200*time.Millisecond, "QoS deadline from actual submit")
	estimate := flag.Duration("estimate", 22*time.Millisecond, "fixed calibrated service estimate")
	rate := flag.Float64("rps", 70, "open-loop offered request rate")
	seed := flag.Int64("seed", 20260928, "offered jitter trace seed")
	tracePath := flag.String("trace", "", "saved offered trace JSON for paired replay")
	workText := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":100}`, "real workload kernel configuration")
	out := flag.String("out", "results-fault", "new run output directory")
	runID := flag.String("run-id", "fault", "prefix for worker and cancellation correlation")
	flag.Parse()
	if *mode == "worker" {
		file, err := os.OpenFile(*workerLog, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
		if err != nil {
			panic(err)
		}
		defer file.Close()
		server := &http.Server{Addr: *listen, Handler: NewWorker(file), ReadHeaderTimeout: 5 * time.Second}
		if err = server.ListenAndServe(); err != nil {
			panic(err)
		}
		return
	}
	if *mode != "controller" || *rate <= 0 {
		panic("invalid mode or offered rate")
	}
	var workload revisionworkload.Config
	if err := json.Unmarshal([]byte(*workText), &workload); err != nil {
		panic(err)
	}
	trace := GenerateTrace(*seed, *rate, 3**phase)
	if *tracePath != "" {
		data, err := os.ReadFile(*tracePath)
		if err != nil {
			panic(err)
		}
		if err = json.Unmarshal(data, &trace); err != nil {
			panic(err)
		}
	}
	config := RunConfig{LocalURL: *local, RemoteURL: *remote, Phase: *phase, Timeout: *timeout, QoS: *qos, Estimate: *estimate, Work: workload, Trace: trace, LocalEligible: *eligible, RunID: *runID}
	if err := os.Mkdir(*out, 0755); err != nil {
		panic(err)
	}
	rows, events, started, err := Run(config)
	if err != nil {
		panic(err)
	}
	if err = WriteResults(*out, config, rows, events, started); err != nil {
		panic(err)
	}
	fmt.Printf("Recorded %d measured adapter request outcomes in %s\n", len(rows), *out)
}
