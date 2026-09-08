package alive_stat

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/onebot"
	"github.com/HakuchumuHYX/HakuBot/utils/rendering"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

const pluginID = "alive_stat"

type processEntry struct {
	Name    string `json:"name"`
	Keyword string `json:"keyword"`
}

type dockerEntry struct {
	Name      string `json:"name"`
	Container string `json:"container"`
}

type config struct {
	AutochatDataFile   string         `json:"autochat_data_file"`
	MonitoredProcesses []processEntry `json:"monitored_processes"`
	DockerProcesses    []dockerEntry  `json:"docker_processes"`
	PingHosts          []string       `json:"ping_hosts"`
}

func Register(app *core.App, prefixes ...string) error {
	paths, err := app.Paths.Plugin(pluginID)
	if err != nil {
		return err
	}
	cfg := config{PingHosts: []string{"baidu.com", "google.com"}}
	if err = utils.ReadJSON(filepath.Join(paths.Config, "config.json"), &cfg); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	runtime, err := loadRuntime(filepath.Join(paths.Data, "stats.json"))
	if err != nil {
		return err
	}
	fontPath := filepath.Join(paths.Resources, "font.ttf")
	if _, err = os.Stat(fontPath); errors.Is(err, os.ErrNotExist) {
		fontPath, err = rendering.FontPath(app.Paths, "Regular")
	}
	if err != nil {
		return err
	}
	if err = app.Runtime.AddCloser(runtime.save); err != nil {
		return err
	}
	if err = app.Runtime.Every("alive_stat-save", 5*time.Minute, func(context.Context) error { return runtime.save() }); err != nil {
		return err
	}
	commands := make([]string, len(prefixes))
	for i, prefix := range prefixes {
		commands[i] = prefix + "alive"
	}
	zero.New().OnMessage(onebot.ExactCommand(commands...)).SetPriority(5).SetBlock(true).Handle(func(bot *zero.Ctx) {
		if bot.Event.GroupID != 0 && !app.Access.Enabled(pluginID, fmt.Sprint(bot.Event.GroupID), fmt.Sprint(bot.Event.UserID)) {
			return
		}
		bot.NoTimeout()
		err := app.Runtime.Do(func(ctx context.Context) error {
			now := time.Now()
			args := strings.ToLower(bot.State["args"].(string))
			night := now.Hour() < 6 || now.Hour() >= 18
			if strings.Contains(args, "night") {
				night = true
			} else if strings.Contains(args, "day") {
				night = false
			}
			server := collect(ctx, cfg)
			if err := ctx.Err(); err != nil {
				return err
			}
			data, err := renderCard(runtime.current(now), autochatRuntime(cfg.AutochatDataFile, now), server, now, night, fontPath)
			if err != nil {
				return err
			}
			if bot.SendChain(message.ImageBytes(data)).ID() == 0 {
				return fmt.Errorf("状态卡发送失败")
			}
			return nil
		})
		if err != nil && !errors.Is(err, context.Canceled) {
			logging.Event(pluginID, bot.Event).WithError(err).Error("生成状态卡失败")
			bot.Send("状态采集或绘图失败，请查看日志。")
		}
	})
	logging.Module(pluginID).Info("状态监控已注册")
	return nil
}
