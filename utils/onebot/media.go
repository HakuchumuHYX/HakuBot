package onebot

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/images"
)

// MediaFiles is only needed when sending shared files rather than ZeroBot ImageBytes.
type MediaFiles struct{ HostRoot, ContainerRoot string }

func (m MediaFiles) Image(data []byte) (string, error) {
	ext, err := images.Validate(data)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	path := filepath.Join(m.HostRoot, "cache/outbound_media", hex.EncodeToString(sum[:])+"."+ext)
	if err = utils.AtomicWrite(path, data); err != nil {
		return "", err
	}
	return m.FileURI(path)
}
func (m MediaFiles) FileURI(path string) (string, error) {
	root, err := filepath.Abs(m.HostRoot)
	if err != nil {
		return "", err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	absolute, err = filepath.EvalSymlinks(absolute)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", errors.New("media must be a regular file")
	}
	rel, err := filepath.Rel(root, absolute)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("media is outside the shared host root")
	}
	container := m.ContainerRoot
	if container == "" {
		container = root
	}
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(filepath.Join(container, rel))}
	return u.String(), nil
}

var ownedMedia = regexp.MustCompile(`^[0-9a-f]{64}\.(png|jpg|gif|webp|bmp)$`)

func (m MediaFiles) Cleanup(now time.Time) error {
	dir := filepath.Join(m.HostRoot, "cache/outbound_media")
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !ownedMedia.MatchString(e.Name()) || !e.Type().IsRegular() {
			continue
		}
		info, err := e.Info()
		if err != nil {
			return err
		}
		if now.Sub(info.ModTime()) > 24*time.Hour {
			if err = os.Remove(filepath.Join(dir, e.Name())); err != nil && !errors.Is(err, os.ErrNotExist) {
				return err
			}
		}
	}
	return nil
}
