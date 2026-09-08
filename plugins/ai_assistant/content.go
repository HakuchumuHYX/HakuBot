package ai_assistant

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image/jpeg"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils/images"
	"github.com/HakuchumuHYX/HakuBot/utils/llm"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/chai2010/webp"
	"github.com/disintegration/imaging"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

const forwardHeader = "【用户回复的合并转发聊天记录】"

type input struct {
	parts      []map[string]any
	texts      []string
	references []llm.Image
}

func (in *input) text(text string, searchable bool) {
	text = strings.TrimSpace(text)
	if text == "" {
		return
	}
	in.parts = append(in.parts, map[string]any{"type": "text", "text": text})
	if searchable {
		in.texts = append(in.texts, text)
	}
}

func (in *input) picture(data []byte, mime string) {
	dataURL := "data:" + mime + ";base64," + base64.StdEncoding.EncodeToString(data)
	in.parts = append(in.parts, map[string]any{
		"type":      "image_url",
		"image_url": map[string]string{"url": dataURL},
	})
	ext := images.ExtensionForMIME(mime)
	in.references = append(in.references, llm.Image{
		Filename: fmt.Sprintf("ref%d.%s", len(in.references)+1, ext),
		MIME:     mime,
		Data:     data,
	})
}

func (p *plugin) prepareImage(ctx context.Context, url string, c Config, generation bool) ([]byte, string, error) {
	ctx, cancel := requestContext(ctx, 20)
	defer cancel()
	raw, err := network.Download(ctx, p.http, url, network.DownloadOptions{Attempts: 1, MaxBytes: 40 << 20})
	if err != nil {
		return nil, "", err
	}
	_, format, err := images.Decode(raw)
	if err != nil {
		return nil, "", err
	}
	img, err := imaging.Decode(bytes.NewReader(raw), imaging.AutoOrientation(true))
	if err != nil {
		return nil, "", err
	}
	maxSize := c.Chat.ImageMaxSize
	preserve := maxSize <= 0
	if generation {
		maxSize = c.Image.ReferenceImageMaxSize
		preserve = true
	}
	resize := maxSize > 0 && max(img.Bounds().Dx(), img.Bounds().Dy()) > maxSize
	supported := format == "jpg" || format == "png" || format == "webp"
	if preserve && !resize && supported {
		return raw, mimeFor(format), nil
	}
	if resize {
		img = imaging.Fit(img, maxSize, maxSize, imaging.Lanczos)
	}
	if !preserve {
		format = "jpg"
	} else if !supported {
		format = "png"
	}
	var b bytes.Buffer
	switch format {
	case "jpg":
		err = jpeg.Encode(&b, images.RGB(img), &jpeg.Options{Quality: 85})
	case "webp":
		err = webp.Encode(&b, img, &webp.Options{Quality: 90})
	default:
		var data []byte
		data, err = images.Encode(img, "png")
		b.Write(data)
	}
	return b.Bytes(), mimeFor(format), err
}

func mimeFor(format string) string {
	if format == "jpg" {
		return "image/jpeg"
	}
	return "image/" + format
}

func (p *plugin) parseInput(ctx context.Context, bot *zero.Ctx, args message.Message, c Config, generation bool) (input, error) {
	var in input
	consume := func(msg message.Message) error {
		for _, part := range msg {
			switch part.Type {
			case "text":
				in.text(part.Data["text"], true)
			case "image":
				if address := part.Data["url"]; address != "" {
					data, mime, err := p.prepareImage(ctx, address, c, generation)
					if err != nil {
						return err
					}
					in.picture(data, mime)
				}
			}
		}
		return nil
	}
	for _, part := range bot.Event.Message {
		if part.Type != "reply" {
			continue
		}
		callCtx, cancel := requestContext(ctx, 60)
		response := bot.CallActionWithContext(callCtx, "get_msg", zero.Params{"message_id": part.Data["id"]})
		cancel()
		if response.Status != "ok" || response.RetCode != 0 {
			return in, fmt.Errorf("获取回复消息失败")
		}
		msg, err := decodeMessage(response.Data.Get("message").Raw)
		if err != nil {
			return in, err
		}
		forward := ""
		if !generation {
			for _, s := range msg {
				if s.Type == "forward" {
					forward = s.Data["id"]
					break
				}
			}
		}
		if forward != "" {
			if err = p.parseForward(ctx, bot, forward, c, &in); err != nil {
				return in, err
			}
		} else if err = consume(msg); err != nil {
			return in, err
		}
		break
	}
	err := consume(args)
	return in, err
}

func decodeMessage(raw string) (message.Message, error) {
	if raw == "" {
		return nil, nil
	}
	if !json.Valid([]byte(raw)) {
		return nil, fmt.Errorf("invalid message JSON")
	}
	return message.ParseMessage([]byte(raw)), nil
}

func (p *plugin) parseForward(ctx context.Context, bot *zero.Ctx, id string, c Config, in *input) error {
	callCtx, cancel := requestContext(ctx, 60)
	rsp := bot.CallActionWithContext(callCtx, "get_forward_msg", zero.Params{"id": id})
	cancel()
	if rsp.Status != "ok" || rsp.RetCode != 0 {
		return fmt.Errorf("获取合并转发失败")
	}
	nodes := rsp.Data.Get("messages").Array()
	lines := []string{forwardHeader}
	seenImages, loaded := 0, 0
	var pictures input
	for i, node := range nodes {
		if i >= c.Chat.ForwardMaxNodes {
			break
		}
		name := ""
		uid := ""
		for _, key := range []string{"sender.card", "sender.nickname", "sender.name", "card", "nickname", "name"} {
			if name = node.Get(key).String(); name != "" {
				break
			}
		}
		for _, key := range []string{"sender.user_id", "user_id", "uin", "qq"} {
			if uid = node.Get(key).String(); uid != "" {
				break
			}
		}
		if name == "" {
			name = uid
		} else if uid != "" {
			name += "(" + uid + ")"
		}
		if name == "" {
			name = "未知用户"
		}
		raw := node.Get("message")
		if !raw.Exists() {
			raw = node.Get("content")
		}
		msg, err := decodeMessage(raw.Raw)
		if err != nil {
			return err
		}
		var pieces []string
		for _, part := range msg {
			switch part.Type {
			case "text":
				pieces = append(pieces, part.Data["text"])
			case "image":
				seenImages++
				pieces = append(pieces, fmt.Sprintf("[图片%d]", seenImages))
				if c.Chat.ForwardIncludeImages && loaded < c.Chat.ForwardMaxImages && part.Data["url"] != "" {
					data, mime, err := p.prepareImage(ctx, part.Data["url"], c, false)
					if err != nil {
						logging.Event(pluginID, bot.Event).WithError(err).Warn("转发图片未能载入")
					} else {
						pictures.picture(data, mime)
						loaded++
					}
				}
			case "at":
				pieces = append(pieces, "[@"+part.Data["qq"]+"]")
			case "face":
				pieces = append(pieces, "[表情:"+part.Data["id"]+"]")
			default:
				pieces = append(pieces, "["+part.Type+"]")
			}
		}
		lines = append(lines, fmt.Sprintf("%d. %s: %s", i+1, name, strings.Join(pieces, " ")))
		if len([]rune(strings.Join(lines, "\n"))) >= c.Chat.ForwardMaxTextChars {
			lines = []string{clip(strings.Join(lines, "\n"), c.Chat.ForwardMaxTextChars), "...[合并转发内容过长，已截断]"}
			break
		}
	}
	if len(nodes) > c.Chat.ForwardMaxNodes {
		lines = append(lines, fmt.Sprintf("...[仅展开前 %d 条合并转发节点]", c.Chat.ForwardMaxNodes))
	}
	if seenImages > loaded {
		lines = append(lines, fmt.Sprintf("...[已展开节点中有 %d 张图片，已传入 %d 张]", seenImages, loaded))
	}
	in.text(strings.Join(lines, "\n"), false)
	in.parts = append(in.parts, pictures.parts...)
	in.references = append(in.references, pictures.references...)
	return nil
}
