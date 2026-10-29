clean:
	rm -rf /dev/mqueue/* && bash ../del-all-ctn.sh

compile:
	go build -o bin/tide cmd/tide/main.go
	go build -o bin/create-tide-cg cmd/cgroup/main.go