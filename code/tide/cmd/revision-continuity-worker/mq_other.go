//go:build !linux

package main

import "fmt"

func servePAIX() error { return fmt.Errorf("PAIX worker requires Linux") }
