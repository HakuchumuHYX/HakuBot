package draw

import (
	"fmt"
	"image"
	"image/color"
	"os"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils/images"
	"github.com/fogleman/gg"
	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
)

// Canvas exposes gg directly instead of reimplementing its painter and gradient system.
func Canvas(width, height int, background color.Color) *gg.Context {
	c := gg.NewContext(width, height)
	c.SetColor(background)
	c.Clear()
	return c
}
func LoadFont(path string, size float64) (font.Face, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	collection, err := opentype.ParseCollection(data)
	if err != nil {
		return nil, err
	}
	f, err := collection.Font(0)
	if err != nil {
		return nil, err
	}
	return opentype.NewFace(f, &opentype.FaceOptions{Size: size, DPI: 72, Hinting: font.HintingFull})
}
func Wrap(c *gg.Context, text string, width float64) []string {
	var lines []string
	for _, paragraph := range strings.Split(text, "\n") {
		line := ""
		for _, r := range paragraph {
			candidate := line + string(r)
			w, _ := c.MeasureString(candidate)
			if w > width && line != "" {
				lines = append(lines, line)
				line = string(r)
			} else {
				line = candidate
			}
		}
		lines = append(lines, line)
	}
	return lines
}
func Text(c *gg.Context, text string, x, y, width, lineHeight float64) float64 {
	for _, line := range Wrap(c, text, width) {
		c.DrawString(line, x, y)
		y += lineHeight
	}
	return y
}
func RoundedBox(c *gg.Context, x, y, w, h, r float64, fill color.Color) {
	c.SetColor(fill)
	c.DrawRoundedRectangle(x, y, w, h, r)
	c.Fill()
}
func PNG(c *gg.Context) ([]byte, error) { return images.Encode(c.Image(), "png") }

type Row struct {
	Name  string
	Count int
}
type Card struct {
	Title, Subtitle, TotalLabel, Footer, Watermark string
	Total                                          int
	Rows                                           []Row
	Width                                          int
	Dark                                           bool
}

func Leaderboard(card Card, fontPath string) ([]byte, error) {
	if card.Width == 0 {
		card.Width = 720
	}
	if card.Width < 240 {
		return nil, fmt.Errorf("card width too small")
	}
	face, err := LoadFont(fontPath, 20)
	if err != nil {
		return nil, err
	}
	defer face.Close()
	measure := gg.NewContext(1, 1)
	measure.SetFontFace(face)
	textWidth := float64(card.Width - 96)
	titleLines := Wrap(measure, card.Title, textWidth)
	subLines := Wrap(measure, card.Subtitle, textWidth)
	footerLines := Wrap(measure, card.Footer, textWidth)
	header := 110 + len(titleLines)*28
	if card.Subtitle != "" {
		header += len(subLines) * 28
	}
	height := header + len(card.Rows)*64 + 80
	if card.Footer != "" {
		height += len(footerLines) * 28
	}
	if len(card.Rows) == 0 {
		height += 40
	}
	bg := color.NRGBA{228, 245, 255, 255}
	fg := color.NRGBA{25, 55, 75, 255}
	panel := color.NRGBA{236, 248, 255, 255}
	if card.Dark {
		bg = color.NRGBA{30, 32, 40, 255}
		fg = color.NRGBA{220, 225, 235, 255}
		panel = color.NRGBA{48, 52, 65, 255}
	}
	c := Canvas(card.Width, height, bg)
	c.SetFontFace(face)
	c.SetColor(fg)
	y := Text(c, card.Title, 40, 50, textWidth, 28)
	if card.Subtitle != "" {
		y = Text(c, card.Subtitle, 40, y+6, textWidth, 28)
	}
	y = Text(c, fmt.Sprintf("%s %d", card.TotalLabel, card.Total), 40, y+16, textWidth, 28) + 20
	for i, row := range card.Rows {
		RoundedBox(c, 24, y-24, float64(card.Width-48), 54, 12, panel)
		c.SetColor(fg)
		c.DrawString(fmt.Sprintf("#%d", i+1), 40, y+10)
		name := row.Name
		for {
			w, _ := c.MeasureString(name)
			if w < textWidth-150 || len([]rune(name)) < 2 {
				break
			}
			r := []rune(name)
			name = string(r[:len(r)-2]) + "…"
		}
		c.DrawString(name, 100, y+10)
		c.DrawStringAnchored(fmt.Sprint(row.Count), float64(card.Width-40), y+10, 1, 0)
		y += 64
	}
	if len(card.Rows) == 0 {
		c.DrawString("暂无排行数据", 40, y)
		y += 40
	}
	if card.Footer != "" {
		y = Text(c, card.Footer, 40, y+10, textWidth, 28)
	}
	if card.Watermark != "" {
		c.DrawStringAnchored(card.Watermark, float64(card.Width-40), float64(height-24), 1, 0)
	}
	return PNG(c)
}
func Horizontal(items []image.Image) (image.Image, error) { return images.Concat(items, "h") }
func Vertical(items []image.Image) (image.Image, error)   { return images.Concat(items, "v") }
