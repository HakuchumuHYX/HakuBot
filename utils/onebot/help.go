package onebot

import (
	"context"
	"fmt"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/rendering"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

func SendHelp(ctx context.Context, bot *zero.Ctx, browser *rendering.Browser, paths utils.Paths, cache *rendering.Cache, doc rendering.HelpDocument, theme string) error {
	rendered, err := browser.Help(ctx, paths, cache, doc, theme, false)
	if err != nil {
		if bot.Send(rendering.HelpText(doc)).ID() == 0 {
			return fmt.Errorf("help render failed (%w) and text delivery failed", err)
		}
		return nil
	}
	for _, p := range rendered.Pages {
		if bot.SendChain(message.ImageBytes(p.Data)).ID() == 0 {
			return fmt.Errorf("help image delivery failed")
		}
	}
	return nil
}
