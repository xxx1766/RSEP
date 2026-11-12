package procmgr

import "time"

type ProcessorSnapshot struct {
	TotalCPUs            int   `json:"total_cpus"`
	ReservedCPUs         int   `json:"reserved_cpus"`
	CommittedCPUs        int   `json:"committed_cpus"`
	BorrowedCPUs         int   `json:"borrowed_cpus"`
	PhysicalOccupiedCPUs int   `json:"physical_occupied_cpus"`
	ObservedNS           int64 `json:"observed_unix_ns"`
}

func (pm *ProcessorManager) Snapshot() ProcessorSnapshot {
	pm.mu.Lock()
	defer pm.mu.Unlock()
	s := ProcessorSnapshot{TotalCPUs: pm.logResTotal.CpuNum, ObservedNS: time.Now().UnixNano()}
	s.ReservedCPUs = s.TotalCPUs - pm.highPrioResRemain.CpuNum
	s.CommittedCPUs = s.TotalCPUs - pm.logResRemain.CpuNum
	s.BorrowedCPUs = s.CommittedCPUs - s.ReservedCPUs
	for _, held := range pm.physResRemain.CpuSet {
		if held != 0 {
			s.PhysicalOccupiedCPUs++
		}
	}
	return s
}
