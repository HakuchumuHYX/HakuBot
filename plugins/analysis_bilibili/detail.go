package analysis_bilibili

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/tidwall/gjson"
	"github.com/wdvxdr1123/ZeroBot/message"
)

type result struct {
	URL     string
	Message message.Message
}

func formatNum(num int64) string {
	if num > 10000 {
		return fmt.Sprintf("%.2f万", float64(num)/10000.0)
	}
	return strconv.FormatInt(num, 10)
}

func fetchLiveUname(ctx context.Context, client *http.Client, uid string) string {
	if uid == "" {
		return ""
	}
	masterURL := fmt.Sprintf("https://api.live.bilibili.com/live_user/v1/Master/info?uid=%s", uid)
	master, err := getJSON(ctx, client, masterURL)
	if err == nil {
		uname := master.Get("data.info.uname").String()
		if uname != "" {
			return uname
		}
	}
	return "UID " + uid
}

func fetchVideoDetail(ctx context.Context, client *http.Client, s *signer, cfg Config, tgt *target, descBlacklisted bool) (result, error) {
	params := map[string]string{}
	if tgt.AID {
		params["aid"] = tgt.ID
	} else {
		params["bvid"] = tgt.ID
	}

	signedQuery, err := s.signQuery(ctx, client, params)
	var apiURL string
	if err != nil {
		logging.Module("analysis_bilibili").Debugf("analysis_bilibili wbi 签名失败，改用未签名 wbi/view: %v", err)
		vals := url.Values{}
		for k, v := range params {
			vals.Set(k, v)
		}
		apiURL = "https://api.bilibili.com/x/web-interface/wbi/view?" + vals.Encode()
	} else {
		apiURL = "https://api.bilibili.com/x/web-interface/wbi/view?" + signedQuery
	}

	payload, err := getJSON(ctx, client, apiURL)
	if err != nil {
		return result{}, err
	}

	res := payload.Get("data")
	if res.Get("v_voucher").Exists() && !res.Get("aid").Exists() {
		return result{}, errors.New("B站风控校验中，请稍后再试")
	}
	aid := res.Get("aid").Int()
	titleStr := res.Get("title").String()
	if payload.Get("code").Int() != 0 || !res.Exists() || !res.IsObject() || aid <= 0 || titleStr == "" {
		return result{}, errors.New("解析到视频被删了/稿件不可见或审核中/权限不足")
	}

	q := url.Values{}
	pages := res.Get("pages").Array()
	var partTitle string
	if tgt.Page >= 1 && tgt.Page <= len(pages) {
		q.Set("p", strconv.Itoa(tgt.Page))
		part := pages[tgt.Page-1].Get("part").String()
		if part != res.Get("title").String() {
			partTitle = fmt.Sprintf("小标题：%s\n", part)
		}
	}
	if tgt.Time != nil && *tgt.Time >= 0 {
		q.Set("t", strconv.Itoa(*tgt.Time))
	}

	standardURL := fmt.Sprintf("https://www.bilibili.com/video/av%d", aid)
	if len(q) > 0 {
		standardURL += "?" + q.Encode()
	}

	hasImage := cfg.IsImageEnabled("video")
	var coverBytes []byte
	if hasImage {
		coverURL := cfg.ResizeImage(res.Get("pic").String(), false)
		if b, err := downloadImage(ctx, client, coverURL); err == nil {
			coverBytes = b
		} else {
			logging.Module("analysis_bilibili").Warnf("下载视频封面失败: %v", err)
		}
	}

	vurlDisplay := standardURL
	if coverBytes != nil {
		vurlDisplay = "\n" + standardURL
	}

	title := fmt.Sprintf("\n标题：%s\n", res.Get("title").String())
	if partTitle != "" {
		title += partTitle
	}
	pubdate := time.Unix(res.Get("pubdate").Int(), 0).Local().Format("2006-01-02 15:04:05")
	tname := fmt.Sprintf("类型：%s | UP：%s | 日期：%s\n", res.Get("tname").String(), res.Get("owner.name").String(), pubdate)

	stat := fmt.Sprintf("播放：%s | 弹幕：%s | 收藏：%s\n点赞：%s | 硬币：%s | 评论：%s\n",
		formatNum(res.Get("stat.view").Int()),
		formatNum(res.Get("stat.danmaku").Int()),
		formatNum(res.Get("stat.favorite").Int()),
		formatNum(res.Get("stat.like").Int()),
		formatNum(res.Get("stat.coin").Int()),
		formatNum(res.Get("stat.reply").Int()),
	)

	descText := ""
	if !descBlacklisted {
		rawDesc := res.Get("desc").String()
		lines := strings.Split(rawDesc, "\n")
		var nonEmpty []string
		for _, l := range lines {
			if strings.TrimSpace(l) != "" {
				nonEmpty = append(nonEmpty, l)
			}
		}
		if len(nonEmpty) > 3 {
			descText = fmt.Sprintf("简介：%s\n%s\n%s……", nonEmpty[0], nonEmpty[1], nonEmpty[2])
		} else if len(nonEmpty) > 0 {
			descText = "简介：" + strings.Join(nonEmpty, "\n")
		}
	}

	msg := message.Message{}
	if coverBytes != nil {
		msg = append(msg, message.ImageBytes(coverBytes))
	}
	msg = append(msg, message.Text(vurlDisplay))
	msg = append(msg, message.Text(title))
	msg = append(msg, message.Text(tname))
	msg = append(msg, message.Text(stat))
	if descText != "" {
		msg = append(msg, message.Text(descText))
	}

	return result{URL: standardURL, Message: msg}, nil
}

func fetchBangumiDetail(ctx context.Context, client *http.Client, cfg Config, tgt *target, descBlacklisted bool) (result, error) {
	var seasonURL string
	if tgt.Kind == "bangumi_md" {
		mediaURL := fmt.Sprintf("https://api.bilibili.com/pgc/review/user?media_id=%s", tgt.ID)
		mediaRes, err := getJSON(ctx, client, mediaURL)
		if err != nil {
			return result{}, err
		}
		seasonID := mediaRes.Get("result.media.season_id").String()
		if seasonID == "" {
			return result{}, errors.New("番剧信息获取失败")
		}
		seasonURL = fmt.Sprintf("https://api.bilibili.com/pgc/view/web/season?season_id=%s", seasonID)
	} else if tgt.Kind == "bangumi_ss" {
		seasonURL = fmt.Sprintf("https://api.bilibili.com/pgc/view/web/season?season_id=%s", tgt.ID)
	} else {
		seasonURL = fmt.Sprintf("https://api.bilibili.com/pgc/view/web/season?ep_id=%s", tgt.ID)
	}

	payload, err := getJSON(ctx, client, seasonURL)
	if err != nil {
		return result{}, err
	}

	res := payload.Get("result")
	if !res.Exists() || res.Type == gjson.Null {
		res = payload.Get("data")
	}
	if !res.Exists() || !res.IsObject() || res.Get("title").String() == "" {
		return result{}, errors.New("番剧信息获取失败")
	}
	if tgt.Kind == "bangumi_md" && res.Get("media_id").Int() == 0 {
		return result{}, errors.New("番剧信息获取失败")
	}
	if tgt.Kind == "bangumi_ss" && res.Get("season_id").Int() == 0 {
		return result{}, errors.New("番剧信息获取失败")
	}

	var standardURL string
	if tgt.Kind == "bangumi_md" {
		standardURL = fmt.Sprintf("https://www.bilibili.com/bangumi/media/md%s", res.Get("media_id").String())
	} else if tgt.Kind == "bangumi_ss" {
		standardURL = fmt.Sprintf("https://www.bilibili.com/bangumi/play/ss%s", res.Get("season_id").String())
	} else {
		standardURL = fmt.Sprintf("https://www.bilibili.com/bangumi/play/ep%s", tgt.ID)
	}
	if tgt.Time != nil && *tgt.Time >= 0 {
		standardURL += fmt.Sprintf("?t=%d", *tgt.Time)
	}

	hasImage := cfg.IsImageEnabled("bangumi")
	var coverBytes []byte
	if hasImage {
		coverURL := cfg.ResizeImage(res.Get("cover").String(), true)
		if b, err := downloadImage(ctx, client, coverURL); err == nil {
			coverBytes = b
		} else {
			logging.Module("analysis_bilibili").Warnf("下载番剧封面失败: %v", err)
		}
	}

	vurlDisplay := standardURL
	if coverBytes != nil {
		vurlDisplay = "\n" + standardURL
	}

	title := fmt.Sprintf("番剧：%s\n", res.Get("title").String())
	longTitle := ""
	if tgt.Kind == "bangumi_ep" {
		for _, ep := range res.Get("episodes").Array() {
			if ep.Get("ep_id").String() == tgt.ID {
				longTitle = fmt.Sprintf("标题：%s\n", ep.Get("long_title").String())
				break
			}
		}
	}
	newEpDesc := fmt.Sprintf("%s\n", res.Get("new_ep.desc").String())

	var styles []string
	for _, st := range res.Get("styles").Array() {
		styles = append(styles, st.String())
	}
	stylesStr := fmt.Sprintf("类型：%s\n", strings.Join(styles, ","))

	evaluate := ""
	if !descBlacklisted {
		evaluate = fmt.Sprintf("简介：%s\n", res.Get("evaluate").String())
	}

	msg := message.Message{}
	if coverBytes != nil {
		msg = append(msg, message.ImageBytes(coverBytes))
	}
	msg = append(msg, message.Text(vurlDisplay+"\n"))
	msg = append(msg, message.Text(title))
	if longTitle != "" {
		msg = append(msg, message.Text(longTitle))
	}
	msg = append(msg, message.Text(newEpDesc))
	msg = append(msg, message.Text(stylesStr))
	if evaluate != "" {
		msg = append(msg, message.Text(evaluate))
	}

	return result{URL: standardURL, Message: msg}, nil
}

func fetchLiveDetail(ctx context.Context, client *http.Client, cfg Config, tgt *target) (result, error) {
	liveURL := fmt.Sprintf("https://api.live.bilibili.com/room/v1/Room/get_info?room_id=%s", tgt.ID)
	payload, err := getJSON(ctx, client, liveURL)
	if err != nil {
		return result{}, err
	}
	if payload.Get("code").Int() != 0 {
		return result{}, errors.New("直播间信息获取失败")
	}

	res := payload.Get("data")
	if !res.Exists() || !res.IsObject() {
		return result{}, errors.New("直播间信息获取失败")
	}
	var room gjson.Result
	var uname string
	var coverSrc string
	var watched string
	var lockStatus, lockTime int64

	if res.Get("room_info").Exists() {
		room = res.Get("room_info")
		uname = res.Get("anchor_info.base_info.uname").String()
		if uname == "" {
			uname = fetchLiveUname(ctx, client, room.Get("uid").String())
		}
		coverSrc = room.Get("cover").String()
		watched = res.Get("watched_show.text_large").String()
		lockStatus = room.Get("lock_status").Int()
		lockTime = room.Get("lock_time").Int()
	} else {
		room = res
		uname = fetchLiveUname(ctx, client, room.Get("uid").String())
		coverSrc = room.Get("user_cover").String()
		if coverSrc == "" {
			coverSrc = room.Get("cover").String()
		}
		lockStatus = room.Get("lock_status").Int()
		lockTime = room.Get("lock_time").Int()
	}

	roomID := room.Get("room_id").Int()
	titleStr := room.Get("title").String()
	uid := room.Get("uid").Int()
	if roomID <= 0 || (titleStr == "" && uid <= 0) {
		return result{}, errors.New("直播间信息获取失败")
	}
	standardURL := fmt.Sprintf("https://live.bilibili.com/%d", roomID)
	liveStatus := room.Get("live_status").Int()
	parentArea := room.Get("parent_area_name").String()
	area := room.Get("area_name").String()
	online := room.Get("online").Int()
	tags := room.Get("tags").String()

	hasImage := cfg.IsImageEnabled("live")
	var coverBytes []byte
	if hasImage && coverSrc != "" {
		coverURL := cfg.ResizeImage(coverSrc, true)
		if b, err := downloadImage(ctx, client, coverURL); err == nil {
			coverBytes = b
		} else {
			logging.Module("analysis_bilibili").Warnf("下载直播封面失败: %v", err)
		}
	}

	var title string
	if lockStatus != 0 {
		if lockTime != 0 {
			lt := time.Unix(lockTime, 0).Local().Format("2006-01-02 15:04:05")
			title = fmt.Sprintf("[已封禁]直播间封禁至：%s\n", lt)
		} else {
			title = fmt.Sprintf("[已封禁]标题：%s\n", titleStr)
		}
	} else if liveStatus == 1 {
		title = fmt.Sprintf("[直播中]标题：%s\n", titleStr)
	} else if liveStatus == 2 {
		title = fmt.Sprintf("[轮播中]标题：%s\n", titleStr)
	} else {
		title = fmt.Sprintf("[未开播]标题：%s\n", titleStr)
	}

	up := fmt.Sprintf("主播：%s  当前分区：%s-%s\n", uname, parentArea, area)
	var watch string
	if watched != "" {
		watch = fmt.Sprintf("观看：%s  直播时的人气上一次刷新值：%s\n", watched, formatNum(online))
	} else {
		watch = fmt.Sprintf("人气：%s\n", formatNum(online))
	}

	vurlDisplay := fmt.Sprintf("https://live.bilibili.com/%d\n", roomID)
	if coverBytes != nil {
		vurlDisplay = "\n" + vurlDisplay
	}

	msg := message.Message{}
	if coverBytes != nil {
		msg = append(msg, message.ImageBytes(coverBytes))
	}
	msg = append(msg, message.Text(vurlDisplay))
	msg = append(msg, message.Text(title))
	msg = append(msg, message.Text(up))
	msg = append(msg, message.Text(watch))
	if tags != "" {
		msg = append(msg, message.Text(fmt.Sprintf("标签：%s\n", tags)))
	}
	if liveStatus != 0 {
		player := fmt.Sprintf("独立播放器：https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid=%d", roomID)
		msg = append(msg, message.Text(player))
	}

	return result{URL: standardURL, Message: msg}, nil
}

func fetchArticleDetail(ctx context.Context, client *http.Client, cfg Config, tgt *target) (result, error) {
	articleURL := fmt.Sprintf("https://api.bilibili.com/x/article/viewinfo?id=%s&mobi_app=pc&from=web", tgt.ID)
	payload, err := getJSON(ctx, client, articleURL)
	if err != nil {
		return result{}, err
	}
	res := payload.Get("data")
	if payload.Get("code").Int() != 0 || !res.Exists() || !res.IsObject() || res.Get("title").String() == "" || res.Get("author_name").String() == "" {
		return result{}, errors.New("专栏信息获取失败")
	}

	standardURL := fmt.Sprintf("https://www.bilibili.com/read/cv%s", tgt.ID)

	hasImage := cfg.IsImageEnabled("article")
	var imagesBytes [][]byte
	if hasImage {
		for _, img := range res.Get("origin_image_urls").Array() {
			resized := cfg.ResizeImage(img.String(), false)
			if b, err := downloadImage(ctx, client, resized); err == nil {
				imagesBytes = append(imagesBytes, b)
			} else {
				logging.Module("analysis_bilibili").Warnf("下载专栏图片失败: %v", err)
			}
		}
	}

	title := fmt.Sprintf("标题：%s\n", res.Get("title").String())
	up := fmt.Sprintf("作者：%s (https://space.bilibili.com/%s)\n", res.Get("author_name").String(), res.Get("mid").String())
	desc := fmt.Sprintf("阅读数：%s 收藏数：%s 硬币数：%s\n分享数：%s 点赞数：%s 不喜欢数：%s\n",
		formatNum(res.Get("stats.view").Int()),
		formatNum(res.Get("stats.favorite").Int()),
		formatNum(res.Get("stats.coin").Int()),
		formatNum(res.Get("stats.share").Int()),
		formatNum(res.Get("stats.like").Int()),
		formatNum(res.Get("stats.dislike").Int()),
	)

	msg := message.Message{}
	for _, imgB := range imagesBytes {
		msg = append(msg, message.ImageBytes(imgB))
	}
	msg = append(msg, message.Text(title))
	msg = append(msg, message.Text(up))
	msg = append(msg, message.Text(desc))
	msg = append(msg, message.Text(standardURL))

	return result{URL: standardURL, Message: msg}, nil
}

func fetchDynamicDetail(ctx context.Context, client *http.Client, cfg Config, tgt *target) (result, error) {
	var dynamicURL string
	if tgt.DynamicByRID {
		dynamicURL = fmt.Sprintf("https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?rid=%s&type=2", tgt.ID)
	} else {
		dynamicURL = fmt.Sprintf("https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=%s", tgt.ID)
	}

	payload, err := getJSON(ctx, client, dynamicURL)
	if err != nil {
		return result{}, err
	}
	if payload.Get("code").Int() != 0 {
		return result{}, errors.New("动态信息获取失败（可能被风控）")
	}

	item := payload.Get("data.item")
	if !item.Exists() || item.Type == gjson.Null || !item.IsObject() {
		return result{}, errors.New("动态内容为空")
	}

	dynamicID := item.Get("id_str").String()
	if dynamicID == "" {
		return result{}, errors.New("动态内容为空")
	}
	standardURL := fmt.Sprintf("https://t.bilibili.com/%s", dynamicID)
	vurl := standardURL + "\n"

	moduleDynamic := item.Get("modules.module_dynamic")
	moduleType := item.Get("type").String()
	desc := moduleDynamic.Get("desc.text").String()
	content := strings.ReplaceAll(strings.ReplaceAll(desc, "\r", "\n"), "\n\n", "\n")

	hasImage := cfg.IsImageEnabled("dynamic")

	var additionalMsg []string
	if add := moduleDynamic.Get("additional"); add.Get("type").String() == "ADDITIONAL_TYPE_GOODS" {
		for _, it := range add.Get("goods.items").Array() {
			additionalMsg = append(additionalMsg, fmt.Sprintf("%s（%s）\n", it.Get("name").String(), it.Get("price").String()))
		}
	}

	var drawImages [][]byte
	var archiveCover []byte
	var archiveMsg string
	split := "\n----------------------------------------\n"
	major := moduleDynamic.Get("major")

	if major.Exists() && major.Type != gjson.Null {
		if moduleType == "DYNAMIC_TYPE_DRAW" {
			if len(additionalMsg) == 0 {
				split = ""
			}
			if hasImage {
				for _, it := range major.Get("draw.items").Array() {
					resized := cfg.ResizeImage(it.Get("src").String(), false)
					if b, err := downloadImage(ctx, client, resized); err == nil {
						drawImages = append(drawImages, b)
					} else {
						logging.Module("analysis_bilibili").Warnf("下载动态图片失败: %v", err)
					}
				}
			} else {
				itemsLen := len(major.Get("draw.items").Array())
				content += fmt.Sprintf("\nPS：动态中包含%d张图片", itemsLen)
			}
		} else if moduleType == "DYNAMIC_TYPE_AV" {
			jumpURL := major.Get("archive.jump_url").String()
			if strings.HasPrefix(jumpURL, "//") {
				jumpURL = "https:" + jumpURL
			}
			if hasImage {
				resized := cfg.ResizeImage(major.Get("archive.cover").String(), false)
				if b, err := downloadImage(ctx, client, resized); err == nil {
					archiveCover = b
				} else {
					logging.Module("analysis_bilibili").Warnf("下载动态视频封面失败: %v", err)
				}
			}
			archiveMsg = fmt.Sprintf("转发视频：%s\n简介：%s", jumpURL, major.Get("archive.desc").String())
		}
	} else if moduleType == "DYNAMIC_TYPE_FORWARD" {
		origID := item.Get("orig.id_str").String()
		if origID != "" {
			archiveMsg = fmt.Sprintf("转发动态：https://t.bilibili.com/%s\n", origID)
		}
	} else {
		split = ""
	}

	msg := message.Message{}
	if content != "" {
		msg = append(msg, message.Text(content))
	}
	for _, d := range drawImages {
		msg = append(msg, message.ImageBytes(d))
	}
	if split != "" {
		msg = append(msg, message.Text(split))
	}
	if archiveCover != nil {
		msg = append(msg, message.ImageBytes(archiveCover))
	}
	if archiveMsg != "" {
		msg = append(msg, message.Text(archiveMsg))
	}
	for _, add := range additionalMsg {
		msg = append(msg, message.Text(add))
	}
	msg = append(msg, message.Text(fmt.Sprintf("\n动态链接：%s", vurl)))

	return result{URL: standardURL, Message: msg}, nil
}
