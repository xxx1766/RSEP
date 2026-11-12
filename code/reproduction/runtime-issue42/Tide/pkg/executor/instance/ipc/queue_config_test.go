package ipc

import "testing"

func TestQueueConfiguration(t *testing.T) {
	t.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192")
	t.Setenv("TIDE_MQ_MAX_MESSAGES", "10")
	a, err := messageQueueAttributes()
	if err != nil || a.MsgSize != 8192 || a.MaxMsg != 10 {
		t.Fatalf("attributes=%+v error=%v", a, err)
	}
	t.Setenv("TIDE_MQ_MESSAGE_BYTES", "-1")
	if _, err := messageQueueAttributes(); err == nil {
		t.Fatal("accepted negative queue size")
	}
	t.Setenv("TIDE_MQ_MESSAGE_BYTES", "not-a-number")
	if _, err := messageQueueAttributes(); err == nil {
		t.Fatal("accepted invalid queue size")
	}
}
