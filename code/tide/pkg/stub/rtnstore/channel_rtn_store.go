package rtnstore

import (
	"sync"

	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

type RoutineStore interface {
	Push(*routine.Routine)
	Pop() <-chan *routine.Routine

	Record(*routine.Routine)

	Get(rtnId string) *routine.Routine
	Exist(rtnId string) bool
}

type chanRtnStore struct {
	rtns sync.Map

	que chan *routine.Routine
}

func New() RoutineStore {
	return newChanStore()
}

func newChanStore() RoutineStore {
	return &chanRtnStore{
		que: make(chan *routine.Routine, 10000),
	}
}

func (q *chanRtnStore) Push(r *routine.Routine) {
	q.que <- r
}

func (q *chanRtnStore) Pop() <-chan *routine.Routine {
	return q.que
}

func (q *chanRtnStore) Record(r *routine.Routine) {
	q.rtns.Store(r.Id, r)
}

func (s *chanRtnStore) Get(rid string) *routine.Routine {
	v, ok := s.rtns.Load(rid)
	if !ok {
		logrus.WithField("RoutineId", rid).Fatal("ChanRtnStore: routine is not existed, ", rid)
	}
	return v.(*routine.Routine)
}

func (s *chanRtnStore) Exist(rid string) (ok bool) {
	_, ok = s.rtns.Load(rid)
	return
}
