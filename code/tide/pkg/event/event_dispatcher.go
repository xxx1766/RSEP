package event

import (
	"strings"
	"sync"

	"github.com/sirupsen/logrus"
)

// EventDispatcher represents an event dispatcher, this can be used to register, remove and dispatch eventhandler.
type EventDispatcher interface {
	RegisterHandler(evtType string, hdlr EventHandler)
	RemoveHandler(evtType string, hdlr EventHandler)
	DispatchEvent(evt *Event)
}

type evtDispatcher struct {
	handlers sync.Map
}

// NewEventDispatcher creates a new event dispatcher.
func NewEventDispatcher() EventDispatcher {
	return &evtDispatcher{}
}

// EventHandler represents an event handler, this can be used to register eventhandler function.
type EventHandler func(evt *Event)

// RegisterHandler registers a handler for certain event type.
func (ed *evtDispatcher) RegisterHandler(evtType string, hdlr EventHandler) {
	if _, ok := ed.handlers.Load(evtType); ok {
		logrus.WithField("EvtType", evtType).Fatal("EvtDispr: register two handler for one event type")
	}
	ed.handlers.Store(evtType, hdlr)
	logrus.WithField("EvtType", evtType).Info("EvtDispr: register handler for the event type")
}

// RemoveHandler removes a handler for certain event type.
func (ed *evtDispatcher) RemoveHandler(evtType string, hdlr EventHandler) {
	if _, ok := ed.handlers.Load(evtType); !ok {
		logrus.WithField("EvtType", evtType).Fatal("EvtDispr: cannot find the handler for the event type")
	}
	ed.handlers.Delete(evtType)
	logrus.WithField("EvtType", evtType).Info("EvtDispr: unregister handler for the event type")
}

// DispatchEvent dispatches a event to the corresponding handler.
func (ed *evtDispatcher) DispatchEvent(evt *Event) {
	logrus.WithFields(logrus.Fields{"EvtType": evt.Type, "From": evt.From}).Trace("try to dispatch a event")
	val, ok := ed.handlers.Load(evt.Type)
	if !ok {
		logrus.WithFields(logrus.Fields{
			"EvtType": evt.Type,
			"From":    evt.From,
		}).Warn("EvtDispr: cannot find the handler for the event type")
		return
	}

	if strings.Contains(evt.Type, "wait") {
		logrus.WithFields(logrus.Fields{
			"EvtType": evt.Type,
			"From":    evt.From,
		}).Debug("Find the handler for the event type")
	}

	if strings.Contains(evt.Type, "create_stub") {
		logrus.WithFields(logrus.Fields{
			"EvtType": evt.Type,
			"From":    evt.From,
		}).Debug("Find the handler for the event type")
	}

	if strings.Contains(evt.Type, "stop_stub") {
		logrus.WithFields(logrus.Fields{
			"EvtType": evt.Type,
			"From":    evt.From,
		}).Debug("Find the handler for the event type")
	}

	hdlr := val.(EventHandler)
	hdlr(evt)
}
