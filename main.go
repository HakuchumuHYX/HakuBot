package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/plugins/ai_assistant"
	"github.com/HakuchumuHYX/HakuBot/plugins/alive_stat"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/sirupsen/logrus"
	zero "github.com/wdvxdr1123/ZeroBot"
)

func main() {
	logging.Init(os.Stderr, logrus.InfoLevel, false)
	if err := run(); err != nil {
		logging.Module("main").WithError(err).Error("HakuBot stopped")
		os.Exit(1)
	}
}
func run() error {
	path := flag.String("config", "config/bot.json", "Bot configuration path")
	levelName := flag.String("log-level", "info", "Log level: trace, debug, info, warn, error, fatal, panic")
	color := flag.Bool("log-color", false, "Enable ANSI colors for console logs")
	flag.Parse()
	level, err := logrus.ParseLevel(*levelName)
	if err != nil {
		return fmt.Errorf("log-level: %w", err)
	}
	logging.Init(os.Stderr, level, *color)
	config, err := core.ReadBotConfig(*path)
	if err != nil {
		return err
	}
	zeroConfig, err := config.ZeroConfig()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var users []string
	for _, id := range config.SuperUsers {
		users = append(users, fmt.Sprint(id))
	}
	app, err := core.Bootstrap(ctx, config.Root, config.Chromium, users)
	if err != nil {
		return err
	}
	if err := app.Runtime.StartPlugin(app.Access, "ai_assistant", func(context.Context) error {
		return ai_assistant.Register(app, config.Prefixes()...)
	}); err != nil {
		logging.Module("ai_assistant").WithError(err).Error("插件初始化失败，未注册 AI 命令")
	}
	if err := app.Runtime.StartPlugin(app.Access, "alive_stat", func(context.Context) error {
		return alive_stat.Register(app, config.Prefixes()...)
	}); err != nil {
		logging.Module("alive_stat").WithError(err).Error("插件初始化失败，未注册监控命令")
	}
	go zero.Run(zeroConfig)
	<-ctx.Done()
	return app.Close()
}
