package stringx

import "strings"

func Concat(strs ...string) string {
	totLen := 0
	for _, str := range strs {
		totLen += len(str)
	}

	var builder strings.Builder
	builder.Grow(totLen)
	for _, str := range strs {
		builder.WriteString(str)
	}
	return builder.String()
}
