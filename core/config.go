package core

import (
	"fmt"
	"net/url"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/driver"
)

type BotConfig struct {
	Root              string   `json:"root"`
	NickName          []string `json:"nickname"`
	CommandPrefix     string   `json:"command_prefix"`
	SuperUsers        []int64  `json:"super_users"`
	WebSocketURL      string   `json:"websocket_url"`
	AccessToken       string   `json:"access_token"`
	ReverseWebSocket  bool     `json:"reverse_websocket"`
	Chromium          string   `json:"chromium"`
	MaxProcessSeconds int      `json:"max_process_seconds"`
}

func ReadBotConfig(path string) (BotConfig, error) {
	var c BotConfig
	err := utils.ReadJSON(path, &c)
	return c, err
}
func (c BotConfig) ZeroConfig() (*zero.Config, error) {
	u, err := url.Parse(c.WebSocketURL)
	if err != nil {
		return nil, err
	}
	if u.Scheme != "ws" && u.Scheme != "wss" && u.Scheme != "ws+unix" {
		return nil, fmt.Errorf("websocket_url must use ws, wss or ws+unix")
	}
	if u.Host == "" {
		return nil, fmt.Errorf("websocket_url requires a host")
	}
	var d zero.Driver = driver.NewWebSocketClient(c.WebSocketURL, c.AccessToken)
	if c.ReverseWebSocket {
		d = driver.NewWebSocketServer(16, c.WebSocketURL, c.AccessToken)
	}
	return &zero.Config{NickName: c.NickName, CommandPrefix: c.CommandPrefix, SuperUsers: c.SuperUsers, Driver: []zero.Driver{d}, MaxProcessTime: time.Duration(c.MaxProcessSeconds) * time.Second}, nil
}
