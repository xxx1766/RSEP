package serve

import (
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/routine"
)

// Config is a struct to hold application configuration from json file
type tideServeConfig struct {
	OutboundAddr string `json:"outbound_ip"`
	Port         int    `json:"port"`
	CpuNum       int    `json:"cpu_num"`
	Addresses    string `json:"addresses"`
	NodeName     string `json:"node_name"`
	SelfRole     clsstat.NodeRole
	SelfRes      *routine.Resource
}
