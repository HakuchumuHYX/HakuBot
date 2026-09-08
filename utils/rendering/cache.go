package rendering

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
)

type Cache struct {
	Directory string
	mu        sync.Mutex
}
type cachedPage struct {
	Width, Height int
	SHA256        string
}

var cacheKey = regexp.MustCompile(`^[0-9a-f]{64}$`)

func (c *Cache) Render(ctx context.Context, key string, force bool, render func() (Document, error)) (Document, error) {
	if !cacheKey.MatchString(key) {
		return Document{}, errors.New("invalid render cache key")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return Document{}, err
	}
	dir := filepath.Join(c.Directory, key)
	manifest := filepath.Join(dir, "pages.json")
	if !force {
		var entries []cachedPage
		if utils.ReadJSON(manifest, &entries) == nil && len(entries) > 0 {
			var d Document
			valid := true
			for i, e := range entries {
				data, err := os.ReadFile(filepath.Join(dir, fmt.Sprintf("%d.png", i)))
				sum := sha256.Sum256(data)
				if err != nil || hex.EncodeToString(sum[:]) != e.SHA256 {
					valid = false
					break
				}
				d.Pages = append(d.Pages, Page{data, e.Width, e.Height})
			}
			if valid {
				now := time.Now()
				if err := os.Chtimes(manifest, now, now); err != nil {
					return Document{}, err
				}
				return d, nil
			}
		}
	}
	d, err := render()
	if err != nil {
		return d, err
	}
	if len(d.Pages) == 0 {
		return d, errors.New("renderer returned no pages")
	}
	if err = os.Remove(manifest); err != nil && !errors.Is(err, os.ErrNotExist) {
		return Document{}, err
	}
	var entries []cachedPage
	for i, p := range d.Pages {
		if err = utils.AtomicWrite(filepath.Join(dir, fmt.Sprintf("%d.png", i)), p.Data); err != nil {
			return Document{}, err
		}
		sum := sha256.Sum256(p.Data)
		entries = append(entries, cachedPage{p.Width, p.Height, hex.EncodeToString(sum[:])})
	}
	return d, utils.WriteJSON(manifest, entries)
}
func (c *Cache) Cleanup(now time.Time) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	entries, err := os.ReadDir(c.Directory)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !e.IsDir() || !cacheKey.MatchString(e.Name()) {
			continue
		}
		dir := filepath.Join(c.Directory, e.Name())
		info, err := os.Stat(filepath.Join(dir, "pages.json"))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		if now.Sub(info.ModTime()) > 7*24*time.Hour {
			if err = os.RemoveAll(dir); err != nil {
				return err
			}
		}
	}
	return nil
}
