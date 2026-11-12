package clsstat

import "github.com/sirupsen/logrus"

type NodeRole int8

const (
	THINGS NodeRole = iota
	EDGE
	CLOUD
	UNKNOWN
)

func ParseRole(s string) (role NodeRole) {
	switch s {
	case "things":
		role = THINGS
	case "edge":
		role = EDGE
	case "cloud":
		role = CLOUD
	default:
		role = UNKNOWN
	}
	return
}

func (r NodeRole) String() string {
	switch r {
	case THINGS:
		return "things"
	case EDGE:
		return "edge"
	case CLOUD:
		return "cloud"
	case UNKNOWN:
		return "unknown"
	}
	logrus.WithField("NodeRole", r).Fatal("not a normal NodeRole")
	return ""
}
