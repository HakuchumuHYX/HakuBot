package core

import (
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sync"

	"github.com/HakuchumuHYX/HakuBot/utils"
)

func LoadConfig[T any](paths utils.Paths, id, filename string) (T, error) {
	var config T
	p, err := paths.Plugin(id)
	if err != nil {
		return config, err
	}
	if filename == "" {
		filename = "config.json"
	}
	if filepath.Base(filename) != filename {
		return config, errors.New("configuration filename must be a basename")
	}
	err = utils.ReadJSON(filepath.Join(p.Config, filename), &config)
	return config, err
}

var settingsMu sync.Mutex

// UpdateFields preserves unknown fields and JSON number precision.
func UpdateFields(path string, changes map[string]json.RawMessage) error {
	settingsMu.Lock()
	defer settingsMu.Unlock()
	var current map[string]json.RawMessage
	if err := utils.ReadJSON(path, &current); err != nil {
		return err
	}
	if current == nil {
		return errors.New("configuration must be an object")
	}
	if err := mergeFields(current, changes); err != nil {
		return err
	}
	return utils.WriteJSON(path, current)
}
func mergeFields(target, patch map[string]json.RawMessage) error {
	for k, v := range patch {
		if !json.Valid(v) {
			return fmt.Errorf("invalid JSON field %s", k)
		}
		var a, b map[string]json.RawMessage
		if json.Unmarshal(target[k], &a) == nil && a != nil && json.Unmarshal(v, &b) == nil && b != nil {
			if err := mergeFields(a, b); err != nil {
				return err
			}
			merged, err := json.Marshal(a)
			if err != nil {
				return err
			}
			target[k] = merged
		} else {
			target[k] = append(json.RawMessage(nil), v...)
		}
	}
	return nil
}
