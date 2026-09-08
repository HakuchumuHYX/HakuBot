package onebot

import (
	"strings"
	"unicode"
	"unicode/utf8"

	zero "github.com/wdvxdr1123/ZeroBot"
)

// ExactCommand matches HakuBot's command boundary: whitespace or end of input.
// Pass the full trigger, including its prefix, when attaching this to OnMessage.
func ExactCommand(commands ...string) zero.Rule {
	return func(ctx *zero.Ctx) bool {
		text := strings.TrimSpace(ctx.ExtractPlainText())
		for _, command := range commands {
			if command == "" || !strings.HasPrefix(text, command) {
				continue
			}
			rest := strings.TrimPrefix(text, command)
			r, _ := utf8.DecodeRuneInString(rest)
			if rest == "" || unicode.IsSpace(r) {
				ctx.State["command"] = command
				ctx.State["args"] = strings.TrimSpace(rest)
				return true
			}
		}
		return false
	}
}
