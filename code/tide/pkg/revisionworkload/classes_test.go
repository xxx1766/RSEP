package revisionworkload

import "testing"

func TestDeadlineClassesRetainAlternatingRequestIdentity(t *testing.T) {
	c, err := ParseDeadlineClasses("100,500")
	if err != nil {
		t.Fatal(err)
	}
	for id := 0; id < 7; id++ {
		fn, ms := c.ForRequest(id, "cpu", 650)
		want := []int{100, 500}[id%2]
		if ms != want || fn != []string{"cpu-qos100", "cpu-qos500"}[id%2] {
			t.Fatalf("id %d: %s %d", id, fn, ms)
		}
	}
	fn, ms := DeadlineClasses(nil).ForRequest(3, "memory", 650)
	if fn != "memory" || ms != 650 {
		t.Fatalf("default changed: %s %d", fn, ms)
	}
}

func TestDeadlineClassesRejectInvalidInputs(t *testing.T) {
	for _, raw := range []string{"0,500", "-1,500", "NaN", "1.5", "100,", "86400001"} {
		if _, err := ParseDeadlineClasses(raw); err == nil {
			t.Fatalf("accepted %q", raw)
		}
	}
}
