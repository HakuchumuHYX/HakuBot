package core

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

type Lifecycle struct {
	ctx      context.Context
	cancel   context.CancelFunc
	mu       sync.Mutex
	stopping bool
	tasks    sync.WaitGroup
	closers  []func() error
}

func NewLifecycle(parent context.Context) *Lifecycle {
	ctx, cancel := context.WithCancel(parent)
	return &Lifecycle{ctx: ctx, cancel: cancel}
}
func (r *Lifecycle) Context() context.Context { return r.ctx }
func (r *Lifecycle) Go(name string, work func(context.Context) error) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.stopping {
		return errors.New("runtime is stopping")
	}
	r.tasks.Add(1)
	go func() {
		defer r.tasks.Done()
		if err := work(r.ctx); err != nil && !errors.Is(err, context.Canceled) {
			logging.Module("lifecycle").WithField("task", name).WithError(err).Error("background task failed")
		}
	}()
	return nil
}
func (r *Lifecycle) Every(name string, interval time.Duration, work func(context.Context) error) error {
	if interval <= 0 {
		return errors.New("interval must be positive")
	}
	return r.Go(name, func(ctx context.Context) error {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-ticker.C:
				if err := work(ctx); err != nil {
					logging.Module("lifecycle").WithField("task", name).WithError(err).Error("scheduled task failed")
				}
			}
		}
	})
}
func (r *Lifecycle) AddCloser(close func() error) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.stopping {
		return errors.New("runtime is stopping")
	}
	r.closers = append(r.closers, close)
	return nil
}
func (r *Lifecycle) Close() error {
	r.mu.Lock()
	if r.stopping {
		r.mu.Unlock()
		return nil
	}
	r.stopping = true
	r.cancel()
	closers := append([]func() error(nil), r.closers...)
	r.mu.Unlock()
	r.tasks.Wait()
	var errs []error
	for i := len(closers) - 1; i >= 0; i-- {
		if err := closers[i](); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}
func (r *Lifecycle) StartPlugin(access *Access, id string, start func(context.Context) error) error {
	if err := start(r.ctx); err != nil {
		access.SetHealth(id, "unavailable: "+err.Error())
		return fmt.Errorf("start %s: %w", id, err)
	}
	access.SetHealth(id, "ready")
	return nil
}
