package serve

import "testing"

func TestValidateCPUCount(t *testing.T) {
	for _, tc := range []struct {
		name    string
		count   int
		allowed []int
		wantErr bool
	}{
		{"explicit subset", 2, []int{0, 1, 2, 3}, false},
		{"all allowed", 4, []int{0, 1, 2, 3}, false},
		{"zero", 0, []int{0, 1}, true},
		{"negative", -1, []int{0, 1}, true},
		{"too many", 3, []int{0, 1}, true},
		{"missing first cpu", 2, []int{2, 3}, true},
		{"hole in selected range", 3, []int{0, 1, 3, 4}, true},
		{"hole outside selected range", 2, []int{0, 1, 3, 4}, false},
		{"no allowed cpus", 1, nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := validateCPUCount(tc.count, tc.allowed)
			if (err != nil) != tc.wantErr {
				t.Fatalf("validateCPUCount(%d, %v) = %v; wantErr %v", tc.count, tc.allowed, err, tc.wantErr)
			}
		})
	}
}
