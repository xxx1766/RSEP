package ipc

import "github.com/ict/tide/pkg/interfaces"

type Queue interface {
	InQue
	OutQue
}

type InQue interface {
	Send([]byte) error
	interfaces.Closer
}

type OutQue interface {
	Receive() ([]byte, error)
	interfaces.Closer
}
