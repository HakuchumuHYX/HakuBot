package images

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"image"
	"image/color"
	"image/color/palette"
	"image/draw"
	"image/gif"
	"image/png"
	"math"

	xdraw "golang.org/x/image/draw"
)

func Resize(src image.Image, width, height int) *image.NRGBA {
	dst := image.NewNRGBA(image.Rect(0, 0, width, height))
	xdraw.CatmullRom.Scale(dst, dst.Bounds(), src, src.Bounds(), draw.Src, nil)
	return dst
}
func CenterCrop(src image.Image, ratio float64) (*image.NRGBA, error) {
	if ratio <= 0 {
		return nil, errors.New("aspect ratio must be positive")
	}
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	if float64(w)/float64(h) > ratio {
		w = int(float64(h) * ratio)
	} else {
		h = int(float64(w) / ratio)
	}
	out := image.NewNRGBA(image.Rect(0, 0, w, h))
	draw.Draw(out, out.Bounds(), src, image.Pt(b.Min.X+(b.Dx()-w)/2, b.Min.Y+(b.Dy()-h)/2), draw.Src)
	return out, nil
}
func Concat(src []image.Image, mode string) (*image.NRGBA, error) {
	if len(src) == 0 {
		return nil, errors.New("no images")
	}
	mw, mh := 0, 0
	for _, im := range src {
		mw = max(mw, im.Bounds().Dx())
		mh = max(mh, im.Bounds().Dy())
	}
	var prepared []image.Image
	w, h, cols := 0, 0, 0
	switch mode {
	case "v":
		w = mw
		for _, im := range src {
			im = Resize(im, mw, max(1, im.Bounds().Dy()*mw/im.Bounds().Dx()))
			prepared = append(prepared, im)
			h += im.Bounds().Dy()
		}
	case "h":
		h = mh
		for _, im := range src {
			im = Resize(im, max(1, im.Bounds().Dx()*mh/im.Bounds().Dy()), mh)
			prepared = append(prepared, im)
			w += im.Bounds().Dx()
		}
	case "g":
		cols = max(1, int(math.Sqrt(float64(len(src)))))
		w = cols * mw
		h = ((len(src) + cols - 1) / cols) * mh
		for _, im := range src {
			prepared = append(prepared, Resize(im, mw, mh))
		}
	default:
		return nil, errors.New("concat mode must be v/h/g")
	}
	out := image.NewNRGBA(image.Rect(0, 0, w, h))
	x, y := 0, 0
	for i, im := range prepared {
		if mode == "g" {
			x = (i % cols) * mw
			y = (i / cols) * mh
		}
		rect := image.Rect(x, y, x+im.Bounds().Dx(), y+im.Bounds().Dy())
		draw.Draw(out, rect, im, im.Bounds().Min, draw.Src)
		if mode == "h" {
			x += im.Bounds().Dx()
		} else if mode == "v" {
			y += im.Bounds().Dy()
		}
	}
	return out, nil
}
func Tint(src image.Image, c color.NRGBA, mix bool) *image.NRGBA {
	out := image.NewNRGBA(image.Rect(0, 0, src.Bounds().Dx(), src.Bounds().Dy()))
	for y := 0; y < out.Bounds().Dy(); y++ {
		for x := 0; x < out.Bounds().Dx(); x++ {
			p := color.NRGBAModel.Convert(src.At(src.Bounds().Min.X+x, src.Bounds().Min.Y+y)).(color.NRGBA)
			if mix {
				a := uint32(c.A)
				p.R = uint8((uint32(p.R)*(255-a) + uint32(c.R)*a) / 255)
				p.G = uint8((uint32(p.G)*(255-a) + uint32(c.G)*a) / 255)
				p.B = uint8((uint32(p.B)*(255-a) + uint32(c.B)*a) / 255)
			} else {
				p.R = uint8(uint32(p.R) * uint32(c.R) / 255)
				p.G = uint8(uint32(p.G) * uint32(c.G) / 255)
				p.B = uint8(uint32(p.B) * uint32(c.B) / 255)
				p.A = uint8(uint32(p.A) * uint32(c.A) / 255)
			}
			out.SetNRGBA(x, y, p)
		}
	}
	return out
}
func SetAlpha(img *image.NRGBA, value uint8, multiply bool) {
	for y := img.Rect.Min.Y; y < img.Rect.Max.Y; y++ {
		for x := img.Rect.Min.X; x < img.Rect.Max.X; x++ {
			i := img.PixOffset(x, y) + 3
			if multiply {
				img.Pix[i] = uint8(uint16(img.Pix[i]) * uint16(value) / 255)
			} else {
				img.Pix[i] = value
			}
		}
	}
}

// GIFFrames composites frame offsets and disposal modes into full-sized images.
func GIFFrames(data []byte) ([]image.Image, []int, error) {
	g, err := gif.DecodeAll(bytes.NewReader(data))
	if err != nil {
		return nil, nil, err
	}
	canvas := image.NewNRGBA(image.Rect(0, 0, g.Config.Width, g.Config.Height))
	var result []image.Image
	for i, frame := range g.Image {
		before := image.NewNRGBA(canvas.Bounds())
		copy(before.Pix, canvas.Pix)
		draw.Draw(canvas, frame.Bounds(), frame, frame.Bounds().Min, draw.Over)
		out := image.NewNRGBA(canvas.Bounds())
		copy(out.Pix, canvas.Pix)
		result = append(result, out)
		if i < len(g.Disposal) {
			switch g.Disposal[i] {
			case gif.DisposalBackground:
				draw.Draw(canvas, frame.Bounds(), image.Transparent, image.Point{}, draw.Src)
			case gif.DisposalPrevious:
				canvas = before
			}
		}
	}
	return result, g.Delay, nil
}
func EncodeGIF(frames []image.Image, delays []int, loop int, threshold uint8) ([]byte, error) {
	if len(frames) == 0 || len(frames) != len(delays) {
		return nil, errors.New("frames and centisecond delays must have equal nonzero lengths")
	}
	g := &gif.GIF{Delay: delays, LoopCount: loop}
	pal := append(color.Palette{color.NRGBA{}}, palette.Plan9[:255]...)
	for _, im := range frames {
		if im.Bounds().Size() != frames[0].Bounds().Size() {
			return nil, errors.New("GIF frame dimensions differ")
		}
		p := image.NewPaletted(image.Rect(0, 0, im.Bounds().Dx(), im.Bounds().Dy()), pal)
		for y := 0; y < p.Rect.Dy(); y++ {
			for x := 0; x < p.Rect.Dx(); x++ {
				c := color.NRGBAModel.Convert(im.At(im.Bounds().Min.X+x, im.Bounds().Min.Y+y)).(color.NRGBA)
				if c.A > threshold {
					c.A = 255
					p.SetColorIndex(x, y, uint8(pal[1:].Index(c)+1))
				}
			}
		}
		g.Image = append(g.Image, p)
		g.Disposal = append(g.Disposal, gif.DisposalBackground)
	}
	var b bytes.Buffer
	err := gif.EncodeAll(&b, g)
	return b.Bytes(), err
}

// EncodeAPNG uses complete RGBA frames and source blending, preserving partial alpha.
func EncodeAPNG(frames []image.Image, delayMS uint16, loop uint32) ([]byte, error) {
	if len(frames) == 0 {
		return nil, errors.New("no APNG frames")
	}
	var out bytes.Buffer
	out.WriteString("\x89PNG\r\n\x1a\n")
	seq := uint32(0)
	chunk := func(kind string, data []byte) {
		binary.Write(&out, binary.BigEndian, uint32(len(data)))
		out.WriteString(kind)
		out.Write(data)
		h := crc32.NewIEEE()
		h.Write([]byte(kind))
		h.Write(data)
		binary.Write(&out, binary.BigEndian, h.Sum32())
	}
	for i, im := range frames {
		if im.Bounds().Size() != frames[0].Bounds().Size() {
			return nil, errors.New("APNG frame dimensions differ")
		}
		rgba := image.NewNRGBA64(image.Rect(0, 0, im.Bounds().Dx(), im.Bounds().Dy()))
		draw.Draw(rgba, rgba.Bounds(), im, im.Bounds().Min, draw.Src)
		// Force RGBA encoding consistently even when a frame is entirely opaque.
		wrapped := rgbaImage{rgba}
		var encoded bytes.Buffer
		if err := png.Encode(&encoded, wrapped); err != nil {
			return nil, err
		}
		data := encoded.Bytes()
		if i == 0 {
			chunk("IHDR", data[16:29])
			var ac bytes.Buffer
			binary.Write(&ac, binary.BigEndian, uint32(len(frames)))
			binary.Write(&ac, binary.BigEndian, loop)
			chunk("acTL", ac.Bytes())
		}
		var fc bytes.Buffer
		for _, v := range []uint32{seq, uint32(im.Bounds().Dx()), uint32(im.Bounds().Dy()), 0, 0} {
			binary.Write(&fc, binary.BigEndian, v)
		}
		seq++
		binary.Write(&fc, binary.BigEndian, delayMS)
		binary.Write(&fc, binary.BigEndian, uint16(1000))
		fc.Write([]byte{0, 0})
		chunk("fcTL", fc.Bytes())
		for pos := 8; pos < len(data); {
			n := int(binary.BigEndian.Uint32(data[pos:]))
			kind := string(data[pos+4 : pos+8])
			payload := data[pos+8 : pos+8+n]
			if kind == "IDAT" {
				if i == 0 {
					chunk("IDAT", payload)
				} else {
					var fd bytes.Buffer
					binary.Write(&fd, binary.BigEndian, seq)
					seq++
					fd.Write(payload)
					chunk("fdAT", fd.Bytes())
				}
			}
			pos += 12 + n
		}
	}
	chunk("IEND", nil)
	return out.Bytes(), nil
}

type rgbaImage struct{ *image.NRGBA64 }

func (r rgbaImage) Opaque() bool { return false }
