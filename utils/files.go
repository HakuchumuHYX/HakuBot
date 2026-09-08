package utils

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

type TemporaryFiles struct {
	mu     sync.Mutex
	files  map[string]struct{}
	closed bool
}

func (t *TemporaryFiles) Create(suffix string) (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed {
		return "", errors.New("temporary files closed")
	}
	f, err := os.CreateTemp("", "hakubot-*"+filepath.Ext("file."+strings.TrimPrefix(suffix, ".")))
	if err != nil {
		return "", err
	}
	if err = f.Close(); err != nil {
		os.Remove(f.Name())
		return "", err
	}
	if t.files == nil {
		t.files = map[string]struct{}{}
	}
	t.files[f.Name()] = struct{}{}
	return f.Name(), nil
}
func (t *TemporaryFiles) Remove(path string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if _, ok := t.files[path]; !ok {
		return errors.New("temporary file is not owned by this manager")
	}
	err := os.Remove(path)
	if err == nil || errors.Is(err, os.ErrNotExist) {
		delete(t.files, path)
		return nil
	}
	return err
}
func (t *TemporaryFiles) Close() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.closed = true
	var errs []error
	for p := range t.files {
		if err := os.Remove(p); err != nil && !errors.Is(err, os.ErrNotExist) {
			errs = append(errs, err)
		} else {
			delete(t.files, p)
		}
	}
	return errors.Join(errs...)
}

// Pool bounds CPU-heavy work; Go does not need a separate Python-style executor.
type Pool struct{ slots chan struct{} }

func NewPool(workers int) *Pool {
	if workers < 1 {
		workers = 1
	}
	return &Pool{make(chan struct{}, workers)}
}
func (p *Pool) Do(ctx context.Context, work func() error) error {
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
		return work()
	case <-ctx.Done():
		return ctx.Err()
	}
}
func Truncate(text string, length int) string {
	r := []rune(text)
	if length < 0 {
		length = 0
	}
	if len(r) <= length {
		return text
	}
	return string(r[:length]) + "..."
}
