package images

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/draw"
	"image/gif"
	"image/jpeg"
	"image/png"
	"strings"

	"golang.org/x/image/bmp"
	_ "golang.org/x/image/webp"
)

func ExtensionForMIME(mime string) string {
	switch strings.TrimSpace(strings.Split(mime, ";")[0]) {
	case "image/jpeg", "image/jpg":
		return "jpg"
	case "image/png":
		return "png"
	case "image/gif":
		return "gif"
	case "image/webp":
		return "webp"
	case "image/bmp":
		return "bmp"
	}
	return ""
}
func Decode(data []byte) (image.Image, string, error) {
	img, format, err := image.Decode(bytes.NewReader(data))
	if format == "jpeg" {
		format = "jpg"
	}
	return img, format, err
}
func Validate(data []byte) (string, error) { _, format, err := Decode(data); return format, err }
func RGB(img image.Image) *image.RGBA {
	out := image.NewRGBA(image.Rect(0, 0, img.Bounds().Dx(), img.Bounds().Dy()))
	draw.Draw(out, out.Bounds(), image.NewUniform(color.White), image.Point{}, draw.Src)
	draw.Draw(out, out.Bounds(), img, img.Bounds().Min, draw.Over)
	return out
}
func Encode(img image.Image, format string) ([]byte, error) {
	var b bytes.Buffer
	var err error
	switch strings.ToLower(format) {
	case "png", "":
		err = png.Encode(&b, img)
	case "jpg", "jpeg":
		err = jpeg.Encode(&b, RGB(img), &jpeg.Options{Quality: 95})
	case "gif":
		err = gif.Encode(&b, img, nil)
	case "bmp":
		err = bmp.Encode(&b, img)
	default:
		err = errors.New("unsupported output image format")
	}
	return b.Bytes(), err
}
