package revisionworkload

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"io"
	"os"
)

type Config struct {
	Kind       string `json:"kind"`
	Bytes      int    `json:"bytes"`
	Iterations int    `json:"iterations"`
	TempDir    string `json:"-"`
}

type Result struct {
	Checksum       uint64 `json:"checksum"`
	BytesProcessed int64  `json:"bytes_processed"`
}

func Run(c Config) (Result, error) {
	if c.Bytes < 8 || c.Bytes > 256<<20 || c.Iterations < 1 || c.Iterations > 100000 {
		return Result{}, fmt.Errorf("invalid workload size or iteration count: %+v", c)
	}
	r := Result{BytesProcessed: int64(c.Bytes) * int64(c.Iterations)}
	switch c.Kind {
	case "cpu":
		buf := make([]byte, c.Bytes)
		for i := range buf {
			buf[i] = byte(i*17 + 3)
		}
		for i := 0; i < c.Iterations; i++ {
			sum := sha256.Sum256(buf)
			copy(buf, sum[:])
			r.Checksum = binary.LittleEndian.Uint64(sum[:8])
		}
	case "memory":
		buf := make([]byte, c.Bytes)
		for it := 0; it < c.Iterations; it++ {
			for i := range buf {
				buf[i] += byte(i + it + 1)
				r.Checksum += uint64(buf[i])
			}
		}
	case "io":
		f, err := os.CreateTemp(c.TempDir, "rsep-workload-*")
		if err != nil {
			return Result{}, err
		}
		defer os.Remove(f.Name())
		defer f.Close()
		buf := make([]byte, c.Bytes)
		for i := range buf {
			buf[i] = byte(i*17 + 3)
		}
		want := crc32.ChecksumIEEE(buf)
		readback := make([]byte, c.Bytes)
		for it := 0; it < c.Iterations; it++ {
			if _, err = f.Seek(0, 0); err != nil {
				return Result{}, err
			}
			if _, err = f.Write(buf); err != nil {
				return Result{}, err
			}
			if err = f.Sync(); err != nil {
				return Result{}, err
			}
			if _, err = f.Seek(0, 0); err != nil {
				return Result{}, err
			}
			if _, err = io.ReadFull(f, readback); err != nil {
				return Result{}, err
			}
			got := crc32.ChecksumIEEE(readback)
			if got != want {
				return Result{}, fmt.Errorf("I/O checksum mismatch")
			}
			r.Checksum += uint64(got)
		}
	default:
		return Result{}, fmt.Errorf("unknown workload %q", c.Kind)
	}
	return r, nil
}
