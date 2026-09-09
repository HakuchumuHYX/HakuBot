package bili_dyn_sub

import (
	"context"
	"fmt"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/HakuchumuHYX/HakuBot/utils/onebot"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

type SendTarget struct {
	GroupID int64
	UserID  int64
}

func (t SendTarget) IsPrivate() bool {
	return t.UserID > 0
}

func (t SendTarget) String() string {
	if t.IsPrivate() {
		return fmt.Sprintf("私聊 %d", t.UserID)
	}
	return fmt.Sprintf("群 %d", t.GroupID)
}

func sendWithRetry(
	ctx context.Context,
	bot *zero.Ctx,
	target SendTarget,
	msg message.Message,
	attempts int,
	interval time.Duration,
	desc string,
) bool {
	if attempts < 1 {
		attempts = 1
	}

	action := "send_group_msg"
	key := "group_id"
	id := target.GroupID
	if target.IsPrivate() {
		action = "send_private_msg"
		key = "user_id"
		id = target.UserID
	}

	for attempt := 1; attempt <= attempts; attempt++ {
		if ctx.Err() != nil {
			return false
		}

		sendCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		r := bot.CallActionWithContext(sendCtx, action, zero.Params{key: id, "message": msg})
		cancel()

		_ = network.Wait(ctx, interval)

		if r.Status == "" {
			logging.Module("bili_dyn_sub").Warnf("%s 发送%s超时或状态未知，跳过重试以防重复", target, desc)
			return true
		}
		if r.Status == "ok" && r.RetCode == 0 {
			return true
		}

		logging.Module("bili_dyn_sub").Warnf("%s 发送%s失败 (第 %d/%d 次, retcode=%d): %s", target, desc, attempt, attempts, r.RetCode, r.Message)
	}

	logging.Module("bili_dyn_sub").Errorf("%s 发送%s最终失败，已尝试 %d 次", target, desc, attempts)
	return false
}

func DispatchSegments(
	ctx context.Context,
	bot *zero.Ctx,
	botID int64,
	botNick string,
	target SendTarget,
	segments []message.Segment,
	attempts int,
	interval time.Duration,
) error {
	if len(segments) == 0 {
		return nil
	}
	if botNick == "" {
		botNick = "Bot"
	}

	sendWithRetry(ctx, bot, target, message.Message{segments[0]}, attempts, interval, "动态卡片")
	if ctx.Err() != nil {
		return ctx.Err()
	}

	rest := segments[1:]
	if len(rest) == 0 {
		return nil
	}
	if len(rest) == 1 {
		sendWithRetry(ctx, bot, target, message.Message{rest[0]}, attempts, interval, "动态配图")
		return nil
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}

	var items []onebot.ForwardItem
	for _, seg := range rest {
		items = append(items, onebot.ForwardItem{
			Name:    botNick,
			UserID:  botID,
			Content: message.Message{seg},
		})
	}

	forwardTarget := onebot.Target{
		GroupID: target.GroupID,
		UserID:  target.UserID,
	}

	opts := onebot.ForwardOptions{
		FallbackInterval:        interval,
		ContinueOnFallbackError: true,
	}

	status, err := onebot.SendForward(ctx, bot, forwardTarget, items, true, opts)
	if err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Warnf("%s 合并转发降级发送部分或全部失败 (status=%s)", target, status)
		return fmt.Errorf("forward delivery to %s failed: %w", target, err)
	}
	return nil
}
