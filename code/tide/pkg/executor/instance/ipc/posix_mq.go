package ipc

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/aceofkid/posix_mq"
	"github.com/ict/tide/pkg/stringx"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

type posixMQ struct {
	name string
	mq   *posix_mq.MessageQueue
}

func newPosixMQ(name string) (que Queue, err error) {
	if strings.Contains(name, "/") {
		return nil, errors.New("message queue cannot contains '/'")
	}
	logger := logrus.WithField("MqName", name)
	logger.Debug("PosixMQ: start creating")
	defer logger.Debug("PosixMQ: finish creating")

	attr, err := messageQueueAttributes()
	if err != nil {
		return nil, err
	}
	oflag := posix_mq.O_RDWR | posix_mq.O_CREAT | posix_mq.O_EXCL
	mode := 0666
	mqName := stringx.Concat("/", name)
	mq, err := posix_mq.NewMessageQueue(mqName, oflag, mode, attr)
	if err != nil {
		return nil, errors.Wrap(err, "create message queue failed")
	}
	return &posixMQ{name: name, mq: mq}, nil
}

// Keep legacy defaults; small-payload experiments can use the host's existing
// queue limits without changing system-wide sysctls. All peers must fit payloads
// within the configured message size.
func messageQueueAttributes() (*posix_mq.MessageQueueAttribute, error) {
	a := &posix_mq.MessageQueueAttribute{MsgSize: 1 << 24, MaxMsg: 65535}
	for _, item := range []struct {
		name   string
		target *int
		max    int
	}{
		{"TIDE_MQ_MESSAGE_BYTES", &a.MsgSize, 1 << 24},
		{"TIDE_MQ_MAX_MESSAGES", &a.MaxMsg, 65536},
	} {
		if s := os.Getenv(item.name); s != "" {
			n, err := strconv.Atoi(s)
			if err != nil || n < 1 || n > item.max {
				return nil, fmt.Errorf("%s must be between 1 and %d", item.name, item.max)
			}
			*item.target = n
		}
	}
	return a, nil
}

func (q *posixMQ) Send(data []byte) error {
	if err := q.mq.Send(data, 0); err != nil {
		return errors.Wrap(err, "posixMQ send message failed")
	}
	return nil
}

func (q *posixMQ) Receive() ([]byte, error) {
	if data, _, err := q.mq.Receive(); err != nil {
		return nil, errors.Wrap(err, "posixMQ receive message failed")
	} else {
		return data, nil
	}
}

func (q *posixMQ) Close() error {
	return q.mq.Unlink()
}
