package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/HakuchumuHYX/HakuBot/core"
	zero "github.com/wdvxdr1123/ZeroBot"
)

func main() {
	if err := run(); err != nil {
		slog.Error("HakuBot stopped", "error", err)
		os.Exit(1)
	}
}
func run() error {
	path := flag.String("config", "config/bot.json", "Bot configuration path")
	flag.Parse()
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
	// Business plugins register here before the transport starts.
	go zero.Run(zeroConfig)
	<-ctx.Done()
	return app.Close()
}
