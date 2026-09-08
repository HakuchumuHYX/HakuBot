package network

import (
	"context"
	"fmt"
	"net/http"

	"github.com/HakuchumuHYX/HakuBot/utils/images"
)

type DownloadedImage struct {
	Data            []byte
	Extension, MIME string
	Width, Height   int
}

func FetchImage(ctx context.Context, client *http.Client, url string, options DownloadOptions) (DownloadedImage, error) {
	data, err := Download(ctx, client, url, options)
	if err != nil {
		return DownloadedImage{}, err
	}
	img, ext, err := images.Decode(data)
	if err != nil {
		return DownloadedImage{}, err
	}
	mime := "image/" + ext
	if ext == "jpg" {
		mime = "image/jpeg"
	}
	return DownloadedImage{data, ext, mime, img.Bounds().Dx(), img.Bounds().Dy()}, nil
}
func Avatar(ctx context.Context, client *http.Client, userID int64, size int) (DownloadedImage, error) {
	if size <= 0 {
		size = 640
	}
	return FetchImage(ctx, client, fmt.Sprintf("https://q1.qlogo.cn/g?b=qq&nk=%d&s=%d", userID, size), DownloadOptions{})
}
