package ai_assistant

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/llm"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/onebot"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

const pluginID = "ai_assistant"

type plugin struct {
	app         *core.App
	mu          sync.RWMutex
	modelChange sync.Mutex
	config      Config
	configPath  string
	chat, image *llm.Client
	http        *http.Client
}

func Register(app *core.App, prefixes ...string) error {
	paths, err := app.Paths.Plugin(pluginID)
	if err != nil {
		return err
	}
	path := filepath.Join(paths.Config, "config.json")
	cfg, err := loadConfig(path)
	if err != nil {
		return err
	}
	chatConfig, _ := cfg.connection(false)
	imageConfig, _ := cfg.connection(true)
	chat, err := llm.New(chatConfig)
	if err != nil {
		return err
	}
	image, err := llm.New(imageConfig)
	if err != nil {
		chat.Close()
		return err
	}
	client, err := app.HTTP.Get(pluginID, optional(cfg.Proxy))
	if err != nil {
		chat.Close()
		image.Close()
		return err
	}
	// Downloads and Tavily calls apply their own context deadlines.
	client.Timeout = 0
	p := &plugin{
		app:        app,
		config:     cfg,
		configPath: path,
		chat:       chat,
		image:      image,
		http:       client,
	}
	if err = app.Runtime.AddCloser(func() error {
		chat.Close()
		return image.Close()
	}); err != nil {
		chat.Close()
		image.Close()
		return err
	}
	engine := zero.New()
	register := func(names []string, feature string, force bool, change bool) {
		commands := make([]string, 0, len(names)*len(prefixes))
		for _, prefix := range prefixes {
			for _, name := range names {
				commands = append(commands, prefix+name)
			}
		}
		rules := []zero.Rule{onebot.ExactCommand(commands...)}
		priority := 5
		if change {
			rules = append(rules, zero.SuperUserPermission)
			priority = 1
		}
		engine.OnMessage(rules...).SetPriority(priority).SetBlock(true).Handle(func(bot *zero.Ctx) {
			if !change && bot.Event.GroupID != 0 {
				group, user := fmt.Sprint(bot.Event.GroupID), fmt.Sprint(bot.Event.UserID)
				if !app.Access.Enabled(pluginID+":"+feature, group, user) {
					return
				}
				if left := app.Access.CheckCD(pluginID+":"+feature, group, user); left > 0 {
					bot.SendChain(message.At(bot.Event.UserID), message.Text(fmt.Sprintf("功能冷却中，请等待 %d 秒", left)))
					return
				}
			}
			bot.NoTimeout()
			cfg := p.snapshot()
			command := bot.State["command"].(string)
			args := commandArgs(bot.Event.Message, command)
			err := app.Runtime.Do(func(ctx context.Context) error {
				var err error
				if change {
					err = p.changeModel(ctx, bot, args.ExtractPlainText())
				} else if feature == "chat" {
					err = p.handleChat(ctx, bot, args, cfg, force)
				} else {
					err = p.handleImage(ctx, bot, args, cfg, force)
				}
				if err != nil && ctx.Err() == nil {
					logging.Event(pluginID, bot.Event).WithError(err).Error("AI 请求失败")
					bot.Send("处理失败：" + clip(err.Error(), 300))
				}
				return nil
			})
			if err != nil {
				logging.Event(pluginID, bot.Event).WithError(err).Warn("无法开始 AI 请求")
				return
			}
		})
	}
	register([]string{"chat"}, "chat", false, false)
	register([]string{"chat联网", "chat_web", "chatweb", "chat搜索"}, "chat", true, false)
	register([]string{"生图"}, "imagen", false, false)
	register([]string{"生图联网", "生图web", "生图搜索"}, "imagen", true, false)
	register([]string{"切换模型", "更改模型", "change_model"}, "", false, true)
	logging.Module(pluginID).Info("AI 助手已注册")
	return nil
}

func (p *plugin) snapshot() Config {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.config
}

func (p *plugin) updateCD(bot *zero.Ctx, feature string) {
	if bot.Event.GroupID == 0 {
		return
	}
	if err := p.app.Access.UpdateCD(pluginID+":"+feature, fmt.Sprint(bot.Event.GroupID), fmt.Sprint(bot.Event.UserID)); err != nil {
		logging.Event(pluginID, bot.Event).WithError(err).Error("模型调用已完成，但保存 CD 失败")
	}
}

func commandArgs(msg message.Message, command string) message.Message {
	result := make(message.Message, 0, len(msg))
	removed := false
	for _, part := range msg {
		if part.Type == "reply" {
			continue
		}
		if !removed && part.Type == "text" {
			text := strings.TrimLeft(part.Data["text"], " \t\n")
			if strings.HasPrefix(text, command) {
				result = append(result, message.Text(strings.TrimSpace(strings.TrimPrefix(text, command))))
				removed = true
				continue
			}
		}
		result = append(result, part)
	}
	return result
}

func (p *plugin) complete(ctx context.Context, c Config, messages []map[string]any, options llm.ChatOptions) (llm.ChatResult, error) {
	if options.Model == "" {
		options.Model = c.Chat.Model
	}
	result, err := p.chat.Chat(ctx, messages, options)
	if err == nil && strings.TrimSpace(result.Content) == "" {
		err = fmt.Errorf("聊天 API 返回成功，但没有文本内容")
	}
	return result, err
}

func (p *plugin) changeModel(ctx context.Context, bot *zero.Ctx, name string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return fmt.Errorf("请提供新的模型名称")
	}
	if !p.modelChange.TryLock() {
		return fmt.Errorf("已有模型切换正在进行")
	}
	defer p.modelChange.Unlock()
	cfg := p.snapshot()
	if cfg.Chat.Model == name {
		bot.Send("当前已经是 " + name + " 模型了")
		return nil
	}
	bot.Send("正在确认模型可用性：" + name)
	result, err := p.complete(ctx, cfg, []map[string]any{{"role": "user", "content": "Hello! This is a connection test."}}, llm.ChatOptions{Model: name})
	if err != nil {
		return fmt.Errorf("切换失败，当前模型保持为 %s：%w", cfg.Chat.Model, err)
	}
	value, err := json.Marshal(map[string]string{"model": name})
	if err != nil {
		return err
	}
	if err = core.UpdateFields(p.configPath, map[string]json.RawMessage{"chat": value}); err != nil {
		return fmt.Errorf("保存失败，当前模型保持为 %s：%w", cfg.Chat.Model, err)
	}
	p.mu.Lock()
	p.config.Chat.Model = name
	p.mu.Unlock()
	bot.Send(fmt.Sprintf("模型切换成功！\n旧模型：%s\n新模型：%s\n响应：%s", cfg.Chat.Model, name, clip(strings.ReplaceAll(result.Content, "\n", " "), 50)))
	return nil
}

func clip(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func clean(s string) string { return strings.Join(strings.Fields(s), " ") }

func system(s string) map[string]any { return map[string]any{"role": "system", "content": s} }

func send(bot *zero.Ctx, msg message.Message) error {
	if bot.Send(msg).ID() == 0 {
		return fmt.Errorf("消息发送失败")
	}
	return nil
}

func requestContext(parent context.Context, seconds float64) (context.Context, context.CancelFunc) {
	return context.WithTimeout(parent, time.Duration(seconds*float64(time.Second)))
}
