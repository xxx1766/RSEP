package revisionworkload

import (
	"fmt"
	"strconv"
	"strings"
)

// DeadlineClasses maps an offered request ID to a fixed deadline class. It is
// shared by experiment adapters; it introduces no priority or reordering.
type DeadlineClasses []int

func ParseDeadlineClasses(raw string) (DeadlineClasses, error) {
	if raw == "" {
		return nil, nil
	}
	var out DeadlineClasses
	for _, part := range strings.Split(raw, ",") {
		ms, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil || ms < 1 || ms > 86400000 {
			return nil, fmt.Errorf("invalid deadline class %q", part)
		}
		out = append(out, ms)
	}
	return out, nil
}

func (c DeadlineClasses) ForRequest(id int, kind string, defaultMS int) (string, int) {
	if len(c) == 0 {
		return kind, defaultMS
	}
	ms := c[id%len(c)]
	return fmt.Sprintf("%s-qos%d", kind, ms), ms
}
