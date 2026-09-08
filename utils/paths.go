package utils

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
)

var identifier = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

type Paths struct{ Root string }

// NewPaths resolves the root once; constructing paths never creates directories.
func NewPaths(root string) (Paths, error) {
	if root == "" {
		root = os.Getenv("HAKUBOT_ROOT")
	}
	if root == "" {
		root = "."
	}
	absolute, err := filepath.Abs(root)
	return Paths{Root: absolute}, err
}

type PluginPaths struct{ Config, Data, Cache, Resources string }

func (p Paths) Plugin(id string) (PluginPaths, error) {
	if !identifier.MatchString(id) {
		return PluginPaths{}, fmt.Errorf("invalid plugin ID %q", id)
	}
	data := id
	switch id {
	case "groupmate_waifu":
		data = "waifu"
	case "alive_stat":
		data = "alive_stats"
	case "sk_predict":
		data = "sekai_cache"
	}
	dataPath := filepath.Join(p.Root, "data", data)
	if id == "identify" {
		dataPath = filepath.Join(p.Root, "plugins", id, "data")
	}
	return PluginPaths{filepath.Join(p.Root, "config", "plugins", id), dataPath, filepath.Join(p.Root, "cache", id), filepath.Join(p.Root, "plugins", id, "resources")}, nil
}
func (p Paths) SharedCache(name string) (string, error) {
	if !identifier.MatchString(name) {
		return "", fmt.Errorf("invalid cache namespace %q", name)
	}
	return filepath.Join(p.Root, "cache", "shared", name), nil
}
