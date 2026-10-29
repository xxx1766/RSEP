package main

import (
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"runtime"
	"sync"

	"github.com/containerd/cgroups"
	"github.com/ict/tide/pkg/containerx"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/stringx"
	"github.com/opencontainers/runtime-spec/specs-go"
	"github.com/sirupsen/logrus"
)

func CreateCgroup(path string) {
	control, err := cgroups.New(cgroups.V1, cgroups.StaticPath(path), &specs.LinuxResources{})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(control.Stat())
}

func DeleteAllCgroup() {
	subsyss := []string{"cpu", "memory", "cpuset"}
	for _, subsys := range subsyss {
		fmt.Println("delete ", subsys)
		DeleteSubsysCgroup(subsys)
	}
}

func DeleteSubsysCgroup(subsys string) {
	tidePath := fmt.Sprintf("/sys/fs/cgroup/%s/tide", subsys)
	if _, err := os.Stat(tidePath); os.IsNotExist(err) {
		fmt.Printf("%s not exist\n", tidePath)
		return
	}

	files, err := ioutil.ReadDir(tidePath)
	if err != nil {
		log.Fatal(err)
	}

	var wg sync.WaitGroup
	for _, file := range files {
		if !file.IsDir() {
			continue
		}

		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			cgpath := stringx.Concat("tide/", name)
			fmt.Println("try to delete cgroup ", cgpath, " ...")
			containerx.DeleteCgroup(cgpath)
		}(file.Name())
	}
	wg.Wait()
}

func main() {
	logrus.SetLevel(logrus.DebugLevel)

	cpuNum := runtime.NumCPU()
	cpuset := make([]int32, cpuNum)
	for i := 0; i < cpuNum; i++ {
		cpuset[i] = 1
	}
	res := &processor.PhysicResource{CpuSet: cpuset}

	DeleteAllCgroup()
	containerx.DeleteCgroup("/tide")
	containerx.CreateCgroup("/tide", res)

	// cpuset = make([]int32, cpuNum)
	// for i := 0; i < 2; i++ {
	// 	cpuset[cpuNum-i-1] = 1
	// }
	// res = &processor.PhysicResource{CpuSet: cpuset}
	containerx.DeleteCgroup("/tide.service")
	containerx.CreateCgroup("/tide.service", res)
}
