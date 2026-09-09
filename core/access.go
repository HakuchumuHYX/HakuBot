package core

import (
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	zero "github.com/wdvxdr1123/ZeroBot"
)

type Access struct {
	mu         sync.RWMutex
	directory  string
	superusers map[string]bool
	status     map[string]map[string]bool
	cooldown   map[string]map[string]int
	timestamps map[string]map[string]map[string]float64
	watermark  map[string]any
	health     map[string]string
}

func NewAccess(paths utils.Paths, superusers []string) (*Access, error) {
	p, err := paths.Plugin("plugin_manager")
	if err != nil {
		return nil, err
	}
	a := &Access{directory: p.Data, superusers: map[string]bool{}, health: map[string]string{}, status: map[string]map[string]bool{}, cooldown: map[string]map[string]int{}, timestamps: map[string]map[string]map[string]float64{}, watermark: map[string]any{"text": "", "position": "bottom_right"}}
	for _, id := range superusers {
		a.superusers[id] = true
	}
	for _, entry := range []struct {
		name   string
		target any
	}{{"plugin_status.json", &a.status}, {"cd_config.json", &a.cooldown}, {"cd_runtime.json", &a.timestamps}, {"watermark_config.json", &a.watermark}} {
		path := filepath.Join(p.Data, entry.name)
		if err := utils.ReadJSON(path, entry.target); err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
	}
	if a.status == nil || a.cooldown == nil || a.timestamps == nil || a.watermark == nil {
		return nil, errors.New("access state must contain JSON objects")
	}
	return a, nil
}

func (a *Access) Superusers() []string {
	a.mu.RLock()
	defer a.mu.RUnlock()
	res := make([]string, 0, len(a.superusers))
	for id := range a.superusers {
		res = append(res, id)
	}
	return res
}

func (a *Access) enabled(id, group, user string) bool {
	if strings.HasPrefix(a.health[id], "unavailable:") {
		return false
	}
	if a.superusers[user] {
		return true
	}
	if v, ok := a.status[id][group]; ok {
		return v
	}
	return true
}
func (a *Access) Enabled(id, group, user string) bool {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if parent, _, ok := strings.Cut(id, ":"); ok {
		return a.enabled(parent, group, user) && a.enabled(id, group, user)
	}
	return a.enabled(id, group, user)
}
func (a *Access) Override(id, group string) (bool, bool) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	v, ok := a.status[id][group]
	return v, ok
}
func (a *Access) SetHealth(id, status string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.health[id] = status
}
func (a *Access) Health() map[string]string {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return maps.Clone(a.health)
}
func (a *Access) SetEnabled(id, group string, enabled bool) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	next := maps.Clone(a.status)
	keys := []string{id}
	if !strings.Contains(id, ":") {
		for _, f := range Features {
			if strings.HasPrefix(f.ID, id+":") {
				keys = append(keys, f.ID)
			}
		}
	}
	for _, k := range keys {
		next[k] = maps.Clone(next[k])
		if next[k] == nil {
			next[k] = map[string]bool{}
		}
		next[k][group] = enabled
	}
	if err := utils.WriteJSON(filepath.Join(a.directory, "plugin_status.json"), next); err != nil {
		return err
	}
	a.status = next
	return nil
}
func (a *Access) Rule(id string) zero.Rule {
	return func(ctx *zero.Ctx) bool {
		return a.Enabled(id, fmt.Sprint(ctx.Event.GroupID), fmt.Sprint(ctx.Event.UserID))
	}
}
func (a *Access) Cooldown(id, group string) int {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.cooldown[group][id]
}
func (a *Access) CheckCD(id, group, user string) int {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.superusers[user] {
		return 0
	}
	remaining := float64(a.cooldown[group][id]) - (float64(time.Now().UnixNano())/1e9 - a.timestamps[group][user][id])
	if remaining <= 0 {
		return 0
	}
	return int(remaining)
}
func (a *Access) SetCD(id, group string, seconds int) error {
	if _, ok := Catalog()[id]; !ok {
		return fmt.Errorf("unknown feature %s", id)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	next := maps.Clone(a.cooldown)
	row := maps.Clone(next[group])
	if row == nil {
		row = map[string]int{}
	}
	if seconds > 0 {
		row[id] = seconds
	} else {
		delete(row, id)
	}
	if len(row) == 0 {
		delete(next, group)
	} else {
		next[group] = row
	}
	if err := utils.WriteJSON(filepath.Join(a.directory, "cd_config.json"), next); err != nil {
		return err
	}
	a.cooldown = next
	return nil
}
func (a *Access) UpdateCD(id, group, user string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.superusers[user] || a.cooldown[group][id] <= 0 {
		return nil
	}
	next := maps.Clone(a.timestamps)
	g := maps.Clone(next[group])
	if g == nil {
		g = map[string]map[string]float64{}
	}
	u := maps.Clone(g[user])
	if u == nil {
		u = map[string]float64{}
	}
	u[id] = float64(time.Now().UnixNano()) / 1e9
	g[user] = u
	next[group] = g
	if err := utils.WriteJSON(filepath.Join(a.directory, "cd_runtime.json"), next); err != nil {
		return err
	}
	a.timestamps = next
	return nil
}
func (a *Access) Watermark() (string, string) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	text, _ := a.watermark["text"].(string)
	position, _ := a.watermark["position"].(string)
	if position == "" {
		position = "bottom_right"
	}
	return text, position
}
func (a *Access) SetWatermark(text, position string) error {
	if position != "bottom_right" && position != "bottom" {
		return errors.New("invalid watermark position")
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	next := maps.Clone(a.watermark)
	next["text"] = text
	next["position"] = position
	if err := utils.WriteJSON(filepath.Join(a.directory, "watermark_config.json"), next); err != nil {
		return err
	}
	a.watermark = next
	return nil
}
