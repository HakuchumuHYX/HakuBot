package draw

import (
	"fmt"
	"image/color"
	"math"
	"os"
	"strings"

	"github.com/fogleman/gg"
	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
)

// Scene measures text while assembling a card, then paints it at its final height.
// Each scene owns its font faces; parallel renders do not share mutable font state.
type Scene struct {
	width    int
	font     *opentype.Font
	faces    map[float64]font.Face
	commands []func(*gg.Context)
	err      error
}

type TextStyle struct {
	Size     float64
	Color    color.Color
	Align    float64 // 0: left, .5: center, 1: right
	Wrap     bool
	Shrink   bool
	MaxLines int
	LineGap  float64
}

type Box struct {
	X, Y, Width, Height, Radius float64
	Fill, Border                color.Color
	BorderWidth                 float64
}

type Background struct {
	Decorations []Box
	Start, End  color.Color
	Panel       *Box
}

func NewScene(width int, fontPath string) (*Scene, error) {
	data, err := os.ReadFile(fontPath)
	if err != nil {
		return nil, err
	}
	collection, err := opentype.ParseCollection(data)
	if err != nil {
		return nil, err
	}
	face, err := collection.Font(0)
	if err != nil {
		return nil, err
	}
	return &Scene{width: width, font: face, faces: make(map[float64]font.Face)}, nil
}

func (s *Scene) Close() {
	for _, face := range s.faces {
		face.Close()
	}
}

func (s *Scene) face(size float64) font.Face {
	if face := s.faces[size]; face != nil {
		return face
	}
	face, err := opentype.NewFace(s.font, &opentype.FaceOptions{Size: size, DPI: 72, Hinting: font.HintingFull})
	if err != nil {
		s.err = err
		return nil
	}
	s.faces[size] = face
	return face
}

// Text uses top coordinates and returns its measured height, including explicit newlines.
func (s *Scene) Text(x, y, width float64, text string, style TextStyle) float64 {
	if s.err != nil {
		return 0
	}
	if width <= 0 || style.Size <= 0 {
		s.err = fmt.Errorf("text width and size must be positive")
		return 0
	}
	measure := gg.NewContext(1, 1)
	size := style.Size
	var lines []string
	var face font.Face
	for {
		face = s.face(size)
		if face == nil {
			return 0
		}
		measure.SetFontFace(face)
		lines = strings.Split(text, "\n")
		if style.Wrap {
			lines = Wrap(measure, text, width)
		}
		fits := style.MaxLines <= 0 || len(lines) <= style.MaxLines
		for _, line := range lines {
			w, _ := measure.MeasureString(line)
			fits = fits && w <= width
		}
		if fits || !style.Shrink || size <= 1 {
			break
		}
		size = math.Max(1, size-0.5)
	}
	metrics := face.Metrics()
	lineHeight := float64(metrics.Ascent+metrics.Descent) / 64
	ascent := float64(metrics.Ascent) / 64
	height := float64(len(lines))*lineHeight + float64(len(lines)-1)*style.LineGap
	s.commands = append(s.commands, func(c *gg.Context) {
		c.SetFontFace(face)
		c.SetColor(style.Color)
		for i, line := range lines {
			w, _ := c.MeasureString(line)
			c.DrawString(line, x+(width-w)*style.Align, y+ascent+float64(i)*(lineHeight+style.LineGap))
		}
	})
	return height
}

func paintBox(c *gg.Context, box Box) {
	c.DrawRoundedRectangle(box.X, box.Y, box.Width, box.Height, box.Radius)
	c.SetColor(box.Fill)
	c.FillPreserve()
	if box.Border != nil && box.BorderWidth > 0 {
		c.SetColor(box.Border)
		c.SetLineWidth(box.BorderWidth)
		c.Stroke()
	} else {
		c.ClearPath()
	}
}

func (s *Scene) Box(box Box) {
	s.commands = append(s.commands, func(c *gg.Context) { paintBox(c, box) })
}

// Backdrop adds a measured panel below already arranged content.
func (s *Scene) Backdrop(box Box) {
	s.commands = append([]func(*gg.Context){func(c *gg.Context) { paintBox(c, box) }}, s.commands...)
}

func (s *Scene) Progress(x, y, width, height, percent float64, background, fill color.Color) {
	percent = math.Max(0, math.Min(100, percent))
	s.Box(Box{X: x, Y: y, Width: width, Height: height, Radius: height / 2, Fill: background})
	if percent > 0 {
		s.Box(Box{X: x, Y: y, Width: math.Min(width, math.Max(height, width*percent/100)), Height: height, Radius: height / 2, Fill: fill})
	}
}

func (s *Scene) PNG(height int, background Background) ([]byte, error) {
	if s.err != nil {
		return nil, s.err
	}
	c := gg.NewContext(s.width, height)
	gradient := gg.NewLinearGradient(0, 0, float64(s.width), float64(height))
	gradient.AddColorStop(0, background.Start)
	gradient.AddColorStop(1, background.End)
	c.SetFillStyle(gradient)
	c.DrawRectangle(0, 0, float64(s.width), float64(height))
	c.Fill()
	for _, decoration := range background.Decorations {
		paintBox(c, decoration)
	}
	if background.Panel != nil {
		paintBox(c, *background.Panel)
	}
	for _, command := range s.commands {
		command(c)
	}
	return PNG(c)
}
