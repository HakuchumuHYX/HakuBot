package onebot

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/network"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

func MentionedUsers(msg message.Message) []int64 {
	var ids []int64
	for _, s := range msg {
		if s.Type == "at" {
			if id, err := strconv.ParseInt(s.Data["qq"], 10, 64); err == nil {
				ids = append(ids, id)
			}
		}
	}
	return ids
}
func ForwardID(msg message.Message) string {
	for _, s := range msg {
		if s.Type == "forward" {
			return s.Data["id"]
		}
	}
	return ""
}
func ImageURLs(msg message.Message) []string {
	var urls []string
	for _, s := range msg {
		if s.Type == "image" && s.Data["url"] != "" {
			urls = append(urls, s.Data["url"])
		}
	}
	return urls
}
func ReplyImages(ctx *zero.Ctx) message.Message {
	for _, s := range ctx.Event.Message {
		if s.Type == "reply" {
			msg := ctx.GetMessage(s.Data["id"])
			var result message.Message
			for _, part := range msg.Elements {
				if part.Type == "image" {
					result = append(result, part)
				}
			}
			return result
		}
	}
	return nil
}
func EventImageURLs(ctx *zero.Ctx, replyFirst bool) []string {
	if replyFirst {
		if urls := ImageURLs(ReplyImages(ctx)); len(urls) > 0 {
			return urls
		}
	}
	if urls := ImageURLs(ctx.Event.Message); len(urls) > 0 {
		return urls
	}
	if !replyFirst {
		return ImageURLs(ReplyImages(ctx))
	}
	return nil
}

type ForwardItem struct {
	Content message.Message
	Name    string
	UserID  int64
}
type Target struct{ GroupID, UserID int64 }
type ForwardStatus string

const (
	Sent           ForwardStatus = "sent"
	FallbackSent   ForwardStatus = "fallback_sent"
	TimeoutUnknown ForwardStatus = "timeout_unknown"
)

func SendForward(ctx context.Context, bot *zero.Ctx, target Target, items []ForwardItem, fallback bool) (ForwardStatus, error) {
	if (target.GroupID == 0) == (target.UserID == 0) || len(items) == 0 {
		return "", errors.New("one target and nonempty forward items are required")
	}
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	nodes := message.Message{}
	for _, item := range items {
		nodes = append(nodes, message.CustomNode(item.Name, item.UserID, item.Content))
	}
	action := "send_group_forward_msg"
	key := "group_id"
	id := target.GroupID
	if id == 0 {
		action = "send_private_forward_msg"
		key = "user_id"
		id = target.UserID
	}
	rsp := bot.CallActionWithContext(ctx, action, zero.Params{key: id, "messages": nodes})
	// ZeroBot reports transport failures as an empty response; never duplicate an unknown delivery.
	if rsp.Status == "" {
		return TimeoutUnknown, nil
	}
	if rsp.RetCode == 0 && rsp.Status == "ok" {
		return Sent, nil
	}
	if !fallback {
		return "", fmt.Errorf("forward rejected (%d): %s", rsp.RetCode, rsp.Message)
	}
	sent := 0
	for i, item := range items {
		action = "send_group_msg"
		if target.GroupID == 0 {
			action = "send_private_msg"
		}
		r := bot.CallActionWithContext(ctx, action, zero.Params{key: id, "message": item.Content})
		if r.Status != "ok" || r.RetCode != 0 {
			return "", fmt.Errorf("forward fallback stopped after %d messages: %s", sent, r.Message)
		}
		sent++
		if i < len(items)-1 {
			if err := network.Wait(ctx, time.Second); err != nil {
				return "", err
			}
		}
	}
	return FallbackSent, nil
}
