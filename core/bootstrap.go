package core

import (
	"context"
	"os"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/HakuchumuHYX/HakuBot/utils/onebot"
	"github.com/HakuchumuHYX/HakuBot/utils/rendering"
)

type App struct {
	Paths       utils.Paths
	Access      *Access
	Runtime     *Lifecycle
	HTTP        *network.Sessions
	Browser     *rendering.Browser
	RenderCache *rendering.Cache
	Temporary   *utils.TemporaryFiles
	Media       *onebot.MediaFiles
}

func Bootstrap(ctx context.Context, root, chromium string, superusers []string) (*App, error) {
	paths, err := utils.NewPaths(root)
	if err != nil {
		return nil, err
	}
	access, err := NewAccess(paths, superusers)
	if err != nil {
		return nil, err
	}
	cacheDir, err := paths.SharedCache("rendering")
	if err != nil {
		return nil, err
	}
	hostRoot := os.Getenv("NAPCAT_SHARED_HOST_ROOT")
	if hostRoot == "" {
		hostRoot = paths.Root
	}
	containerRoot := os.Getenv("NAPCAT_SHARED_CONTAINER_ROOT")
	if containerRoot == "" {
		containerRoot = hostRoot
	}
	a := &App{Paths: paths, Access: access, Runtime: NewLifecycle(ctx), HTTP: &network.Sessions{}, Browser: rendering.NewBrowser(chromium), RenderCache: &rendering.Cache{Directory: cacheDir}, Temporary: &utils.TemporaryFiles{}, Media: &onebot.MediaFiles{HostRoot: hostRoot, ContainerRoot: containerRoot}}
	for _, close := range []func() error{a.Temporary.Close, a.HTTP.Close, a.Browser.Close} {
		if err = a.Runtime.AddCloser(close); err != nil {
			return nil, err
		}
	}
	if err = a.Runtime.Every("render-cache-cleanup", time.Hour, func(context.Context) error {
		if err := a.RenderCache.Cleanup(time.Now()); err != nil {
			return err
		}
		return a.Media.Cleanup(time.Now())
	}); err != nil {
		a.Runtime.Close()
		return nil, err
	}
	return a, nil
}
func (a *App) Close() error { return a.Runtime.Close() }
