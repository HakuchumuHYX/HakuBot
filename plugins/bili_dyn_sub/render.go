package bili_dyn_sub

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"math"
	"net/http"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/images"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/HakuchumuHYX/HakuBot/utils/rendering"
	"github.com/wdvxdr1123/ZeroBot/message"
)

const (
	PlatformName          = "B站"
	Separator             = "--------------"
	DefaultTruncateLength = 500
	MinImageBytes         = 4096
)

func truncateText(content string, limit int) string {
	if limit <= 0 {
		limit = DefaultTruncateLength
	}
	runes := []rune(content)
	if len(runes) < limit {
		return content
	}
	return string(runes[:limit]) + "..."
}

func detailURL(parsed *ParsedDynamic) string {
	if parsed == nil {
		return ""
	}
	if parsed.MajorURL != "" {
		return parsed.MajorURL
	}
	return parsed.URL
}

func BuildText(parsed ParsedDynamic, truncateLen int) string {
	var sb strings.Builder

	if parsed.Title != "" {
		sb.WriteString(parsed.Title)
		sb.WriteString("\n\n")
	}
	sb.WriteString(truncateText(parsed.Content, truncateLen))

	if rp := parsed.Repost; rp != nil {
		sb.WriteString("\n")
		sb.WriteString(Separator)
		sb.WriteString("\n转发自 ")
		sb.WriteString(rp.Nickname)
		sb.WriteString(":\n")
		if rp.Title != "" {
			sb.WriteString(rp.Title)
			sb.WriteString("\n\n")
		}
		rpContent := rp.Content
		if rp.IsDeletedSource && rpContent == "" {
			rpContent = DeletedSourceTips
		}
		sb.WriteString(truncateText(rpContent, truncateLen))
	}

	sb.WriteString("\n")
	sb.WriteString(Separator)
	sb.WriteString("\n来源: ")
	sb.WriteString(PlatformName)
	sb.WriteString(" ")
	sb.WriteString(parsed.Nickname)
	sb.WriteString("\n")

	var urls []string
	if rp := parsed.Repost; rp != nil {
		if rpURL := detailURL(rp); rpURL != "" {
			urls = append(urls, "转发详情："+rpURL)
		}
	}
	if postURL := detailURL(&parsed); postURL != "" {
		urls = append(urls, "详情: "+postURL)
	}
	if len(urls) > 0 {
		sb.WriteString(strings.Join(urls, "\n"))
	}

	return sb.String()
}

func RenderTextImage(ctx context.Context, browser *rendering.Browser, text string) ([]byte, error) {
	if strings.TrimSpace(text) == "" {
		return nil, errors.New("待渲染文本为空，拒绝生成空白图")
	}
	if browser == nil {
		return nil, errors.New("browser 实例未初始化")
	}

	opts := rendering.Options{
		Width:   500,
		Height:  10,
		Scale:   2,
		Timeout: 30 * time.Second,
	}

	data, err := browser.Text(ctx, text, "", opts)
	if err != nil {
		return nil, err
	}
	if len(data) < MinImageBytes {
		return nil, fmt.Errorf("文字卡片产物过小（%d 字节），疑似空白图", len(data))
	}
	if _, err := images.Validate(data); err != nil {
		return nil, fmt.Errorf("文字卡片图片校验失败: %w", err)
	}
	return data, nil
}

func checkImageSquare(img image.Image) bool {
	b := img.Bounds()
	w := b.Dx()
	h := b.Dy()
	if w <= 0 || h <= 0 {
		return false
	}
	diff := math.Abs(float64(w - h))
	return diff/float64(w) < 0.05
}

func isMergable(urls []string) bool {
	for _, u := range urls {
		if !strings.HasPrefix(u, "http://") && !strings.HasPrefix(u, "https://") {
			return false
		}
	}
	return true
}

func toRGB(src image.Image, x, y int) (uint8, uint8, uint8) {
	if nrgba, ok := src.(*image.NRGBA); ok {
		c := nrgba.NRGBAAt(x, y)
		return c.R, c.G, c.B
	}
	c := color.NRGBAModel.Convert(src.At(x, y)).(color.NRGBA)
	return c.R, c.G, c.B
}

func composeGrid(images []image.Image, cols, rows int, xCoord, yCoord []int) ([]byte, error) {
	totalW := xCoord[len(xCoord)-1]
	totalH := yCoord[len(yCoord)-1]
	target := image.NewRGBA(image.Rect(0, 0, totalW, totalH))

	for y := 0; y < rows; y++ {
		for x := 0; x < cols; x++ {
			src := images[y*cols+x]
			srcBounds := src.Bounds()
			dstX := xCoord[x]
			dstY := yCoord[y]
			for sy := 0; sy < srcBounds.Dy(); sy++ {
				for sx := 0; sx < srcBounds.Dx(); sx++ {
					r, g, b := toRGB(src, srcBounds.Min.X+sx, srcBounds.Min.Y+sy)
					target.SetRGBA(dstX+sx, dstY+sy, color.RGBA{
						R: r,
						G: g,
						B: b,
						A: 255,
					})
				}
			}
		}
	}

	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, target, &jpeg.Options{Quality: 75}); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func MergePics(ctx context.Context, client *http.Client, picURLs []string) ([]any, error) {
	var validURLs []string
	for _, u := range picURLs {
		trimmed := strings.TrimSpace(u)
		if trimmed != "" {
			validURLs = append(validURLs, trimmed)
		}
	}
	if len(validURLs) < 3 || !isMergable(validURLs) {
		res := make([]any, len(validURLs))
		for i, u := range validURLs {
			res[i] = u
		}
		return res, nil
	}

	loaded := make(map[int]image.Image)
	loadImage := func(idx int) (image.Image, error) {
		if img, ok := loaded[idx]; ok {
			return img, nil
		}
		data, err := network.Download(ctx, client, validURLs[idx], network.DownloadOptions{Attempts: 2})
		if err != nil {
			return nil, err
		}
		img, _, err := images.Decode(data)
		if err != nil {
			return nil, err
		}
		loaded[idx] = img
		return img, nil
	}

	firstImg, err := loadImage(0)
	if err != nil || !checkImageSquare(firstImg) {
		res := make([]any, len(validURLs))
		for i, u := range validURLs {
			res[i] = u
		}
		return res, nil
	}

	row1Images := []image.Image{firstImg}
	for i := 1; i < 3; i++ {
		img, err := loadImage(i)
		if err != nil || !checkImageSquare(img) || img.Bounds().Dy() != firstImg.Bounds().Dy() {
			res := make([]any, len(validURLs))
			for j, u := range validURLs {
				res[j] = u
			}
			return res, nil
		}
		row1Images = append(row1Images, img)
	}

	xCoord := []int{0}
	tmpX := 0
	for i := 0; i < 3; i++ {
		tmpX += row1Images[i].Bounds().Dx()
		xCoord = append(xCoord, tmpX)
	}
	yCoord := []int{0, firstImg.Bounds().Dy()}

	gridImages := append([]image.Image(nil), row1Images...)
	rows := 1

	processRow := func(row int) bool {
		if len(validURLs) < (row+1)*3 {
			return false
		}
		rowFirst, err := loadImage(row * 3)
		if err != nil || !checkImageSquare(rowFirst) || rowFirst.Bounds().Dx() != gridImages[0].Bounds().Dx() {
			return false
		}
		var curRow []image.Image
		curRow = append(curRow, rowFirst)
		for i := row*3 + 1; i < row*3+3; i++ {
			img, err := loadImage(i)
			if err != nil || !checkImageSquare(img) || img.Bounds().Dy() != rowFirst.Bounds().Dy() || img.Bounds().Dx() != gridImages[i%3].Bounds().Dx() {
				return false
			}
			curRow = append(curRow, img)
		}
		gridImages = append(gridImages, curRow...)
		yCoord = append(yCoord, yCoord[len(yCoord)-1]+rowFirst.Bounds().Dy())
		return true
	}

	if processRow(1) {
		rows = 2
		if processRow(2) {
			rows = 3
		}
	}

	merged, err := composeGrid(gridImages, 3, rows, xCoord, yCoord)
	if err != nil {
		res := make([]any, len(validURLs))
		for i, u := range validURLs {
			res[i] = u
		}
		return res, nil
	}

	logging.Module("bili_dyn_sub").Infof("触发图片合并：3×%d，合并 %d 张", rows, 3*rows)
	var out []any
	out = append(out, merged)
	for _, u := range validURLs[3*rows:] {
		out = append(out, u)
	}
	return out, nil
}

func BuildMessages(ctx context.Context, app *core.App, client *http.Client, parsed ParsedDynamic, truncateLen int) ([]message.Segment, error) {
	text := BuildText(parsed, truncateLen)
	var segments []message.Segment

	card, err := RenderTextImage(ctx, app.Browser, text)
	if err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Warnf("动态 %s 文字卡片渲染失败，降级为纯文本", parsed.DynID)
		segments = append(segments, message.Text(text))
	} else {
		segments = append(segments, message.ImageBytes(card))
	}

	var picGroups [][]string
	if len(parsed.Pics) > 0 {
		picGroups = append(picGroups, append([]string(nil), parsed.Pics...))
	}
	if parsed.Repost != nil && len(parsed.Repost.Pics) > 0 {
		picGroups = append(picGroups, append([]string(nil), parsed.Repost.Pics...))
	}

	for _, pics := range picGroups {
		mergedItems, err := MergePics(ctx, client, pics)
		if err != nil {
			for _, p := range pics {
				segments = append(segments, message.Image(p))
			}
			continue
		}
		for _, item := range mergedItems {
			switch v := item.(type) {
			case []byte:
				segments = append(segments, message.ImageBytes(v))
			case string:
				segments = append(segments, message.Image(v))
			}
		}
	}

	return segments, nil
}
