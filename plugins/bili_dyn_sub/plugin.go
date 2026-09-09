package bili_dyn_sub

import (
	"path/filepath"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

const PluginID = "bili_dyn_sub"

func Register(app *core.App, prefixes ...string) error {
	paths, err := app.Paths.Plugin(PluginID)
	if err != nil {
		return err
	}

	cfg := LoadConfig(filepath.Join(paths.Config, "config.json"))
	bm := NewBackoffManager()
	store := NewStore(filepath.Join(paths.Data, "state.json"), &cfg, bm)
	credMgr := NewCredentialManager(filepath.Join(paths.Data, "credential.json"), &cfg, app.Browser)

	api, err := NewAPIClient(&cfg, credMgr)
	if err != nil {
		return err
	}

	if err := app.Runtime.AddCloser(func() error {
		api.Close()
		return nil
	}); err != nil {
		return err
	}
	if err := app.Runtime.AddCloser(credMgr.Flush); err != nil {
		return err
	}
	if err := app.Runtime.AddCloser(store.Flush); err != nil {
		return err
	}

	registerCommands(app, store, credMgr, api, bm, prefixes)

	polling := NewPollingService(app, &cfg, store, credMgr, api, bm)
	if err := polling.Start(app.Runtime.Context()); err != nil {
		return err
	}

	logging.Module(PluginID).Info("B 站动态订阅插件已就绪")
	return nil
}
