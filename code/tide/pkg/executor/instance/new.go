package instance

import "github.com/ict/tide/pkg/interfaces"

type NewInstanceArgs struct {
	Id         string
	ImageName  string
	VolumePair string
	Commands   []string
	Mode       interfaces.StubMode
}

func New(args *NewInstanceArgs) (Instance, error) {
	return newContainerIns(args)
}
