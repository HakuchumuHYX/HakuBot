package bili_dyn_sub

import (
	"encoding/json"
	"math/big"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/tidwall/gjson"
)

const (
	TypeForward        = "DYNAMIC_TYPE_FORWARD"
	TypeNone           = "DYNAMIC_TYPE_NONE"
	DeletedSourceTips  = "源动态已被删除"
	VideoTextSplit     = "\n=================\n"
	SimilarityMaxChars = 1000
	MaxRepostDepth     = 1
)

var categoryMap = map[string]int{
	"DYNAMIC_TYPE_DRAW":            1,
	"DYNAMIC_TYPE_COMMON_SQUARE":   1,
	"DYNAMIC_TYPE_COMMON_VERTICAL": 1,
	"DYNAMIC_TYPE_ARTICLE":         2,
	"DYNAMIC_TYPE_AV":              3,
	"DYNAMIC_TYPE_WORD":            4,
	"DYNAMIC_TYPE_FORWARD":         5,
	"DYNAMIC_TYPE_LIVE":            6,
	"DYNAMIC_TYPE_LIVE_RCMD":       6,
}

var CategoryNames = map[int]string{
	1: "一般动态",
	2: "专栏文章",
	3: "视频",
	4: "纯文字",
	5: "转发",
	6: "直播推送",
}

var skipTypes = map[string]bool{
	"DYNAMIC_TYPE_LIVE_RCMD": true,
	"DYNAMIC_TYPE_LIVE":      true,
	"DYNAMIC_TYPE_AD":        true,
	"DYNAMIC_TYPE_BANNER":    true,
	TypeNone:                 true,
}

type ParsedDynamic struct {
	DynID           string         `json:"dyn_id"`
	UID             string         `json:"uid"`
	Category        int            `json:"category"`
	DynType         string         `json:"dyn_type"`
	IsPinned        bool           `json:"is_pinned"`
	PubTS           int64          `json:"pub_ts"`
	Nickname        string         `json:"nickname"`
	Title           string         `json:"title"`
	Content         string         `json:"content"`
	Pics            []string       `json:"pics"`
	URL             string         `json:"url"`
	MajorURL        string         `json:"major_url"`
	Repost          *ParsedDynamic `json:"repost,omitempty"`
	IsDeletedSource bool           `json:"is_deleted_source"`
	ParseDegraded   bool           `json:"parse_degraded"`
}

var escapeRegex = regexp.MustCompile(`\\[rnt]|\\u[0-9a-fA-F]{4}`)

func decodeEscapes(s string) string {
	if !strings.Contains(s, `\`) {
		return s
	}
	return escapeRegex.ReplaceAllStringFunc(s, func(m string) string {
		switch m {
		case `\r`:
			return "\r"
		case `\n`:
			return "\n"
		case `\t`:
			return "\t"
		}
		if strings.HasPrefix(m, `\u`) && len(m) == 6 {
			val, err := strconv.ParseInt(m[2:], 16, 32)
			if err == nil {
				return string(rune(val))
			}
		}
		return m
	})
}

func normalizeURL(raw string, dropQuery bool) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	if strings.HasPrefix(s, "//") {
		s = "https:" + s
	} else if strings.HasPrefix(s, "http://") {
		s = "https://" + s[7:]
	} else if !strings.HasPrefix(s, "https://") {
		s = "https://" + strings.TrimLeft(s, "/")
	}
	if dropQuery {
		if u, err := url.Parse(s); err == nil {
			u.RawQuery = ""
			u.Fragment = ""
			return u.String()
		}
	}
	return s
}

// SequenceMatcher implements Python's difflib.SequenceMatcher on []rune with autojunk.
type runeMatch struct {
	a, b, size int
}

type sequenceMatcher struct {
	a, b     []rune
	b2j      map[rune][]int
	bjunk    map[rune]bool
	autojunk bool
}

func newSequenceMatcher(a, b []rune, autojunk bool) *sequenceMatcher {
	sm := &sequenceMatcher{
		a:        a,
		b:        b,
		autojunk: autojunk,
		b2j:      make(map[rune][]int),
		bjunk:    make(map[rune]bool),
	}
	sm.chainB()
	return sm
}

func (sm *sequenceMatcher) chainB() {
	for i, elt := range sm.b {
		sm.b2j[elt] = append(sm.b2j[elt], i)
	}
	n := len(sm.b)
	if sm.autojunk && n >= 200 {
		ntest := n/100 + 1
		for elt, idxs := range sm.b2j {
			if len(idxs) > ntest {
				sm.bjunk[elt] = true
				delete(sm.b2j, elt)
			}
		}
	}
}

func (sm *sequenceMatcher) findLongestMatch(alo, ahi, blo, bhi int) runeMatch {
	besti, bestj, bestsize := alo, blo, 0
	j2len := make(map[int]int)

	for i := alo; i < ahi; i++ {
		newj2len := make(map[int]int)
		for _, j := range sm.b2j[sm.a[i]] {
			if j < blo {
				continue
			}
			if j >= bhi {
				break
			}
			k := j2len[j-1] + 1
			newj2len[j] = k
			if k > bestsize {
				besti = i - k + 1
				bestj = j - k + 1
				bestsize = k
			}
		}
		j2len = newj2len
	}

	for besti > alo && bestj > blo && !sm.bjunk[sm.b[bestj-1]] && sm.a[besti-1] == sm.b[bestj-1] {
		besti--
		bestj--
		bestsize++
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi && !sm.bjunk[sm.b[bestj+bestsize]] && sm.a[besti+bestsize] == sm.b[bestj+bestsize] {
		bestsize++
	}

	for besti > alo && bestj > blo && sm.bjunk[sm.b[bestj-1]] && sm.a[besti-1] == sm.b[bestj-1] {
		besti--
		bestj--
		bestsize++
	}
	for besti+bestsize < ahi && bestj+bestsize < bhi && sm.bjunk[sm.b[bestj+bestsize]] && sm.a[besti+bestsize] == sm.b[bestj+bestsize] {
		bestsize++
	}

	return runeMatch{a: besti, b: bestj, size: bestsize}
}

func (sm *sequenceMatcher) getMatchingBlocks() []runeMatch {
	la, lb := len(sm.a), len(sm.b)
	queue := [][4]int{{0, la, 0, lb}}
	var matchingBlocks []runeMatch

	for len(queue) > 0 {
		top := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		alo, ahi, blo, bhi := top[0], top[1], top[2], top[3]
		x := sm.findLongestMatch(alo, ahi, blo, bhi)
		if x.size > 0 {
			matchingBlocks = append(matchingBlocks, x)
			if alo < x.a && blo < x.b {
				queue = append(queue, [4]int{alo, x.a, blo, x.b})
			}
			if x.a+x.size < ahi && x.b+x.size < bhi {
				queue = append(queue, [4]int{x.a + x.size, ahi, x.b + x.size, bhi})
			}
		}
	}

	sort.Slice(matchingBlocks, func(i, j int) bool {
		if matchingBlocks[i].a != matchingBlocks[j].a {
			return matchingBlocks[i].a < matchingBlocks[j].a
		}
		return matchingBlocks[i].b < matchingBlocks[j].b
	})

	var nonAdjacent []runeMatch
	i1, j1, k1 := 0, 0, 0
	for _, m := range matchingBlocks {
		if i1+k1 == m.a && j1+k1 == m.b {
			k1 += m.size
		} else {
			if k1 > 0 {
				nonAdjacent = append(nonAdjacent, runeMatch{a: i1, b: j1, size: k1})
			}
			i1, j1, k1 = m.a, m.b, m.size
		}
	}
	if k1 > 0 {
		nonAdjacent = append(nonAdjacent, runeMatch{a: i1, b: j1, size: k1})
	}
	return nonAdjacent
}

func textSimilarity(str1, str2 string) float64 {
	r1 := []rune(str1)
	r2 := []rune(str2)
	if len(r1) == 0 || len(r2) == 0 {
		return 0.0
	}
	if len(r1) > SimilarityMaxChars {
		r1 = r1[:SimilarityMaxChars]
	}
	if len(r2) > SimilarityMaxChars {
		r2 = r2[:SimilarityMaxChars]
	}
	sm := newSequenceMatcher(r1, r2, true)
	blocks := sm.getMatchingBlocks()
	matched := 0
	for _, b := range blocks {
		matched += b.size
	}
	minLen := min(len(r1), len(r2))
	if minLen == 0 {
		return 0.0
	}
	return float64(matched) / float64(minLen)
}

func processVideoText(dynamic, desc, title string) (string, string) {
	titleRunes := []rune(title)
	descRunes := []rune(desc)
	if len(titleRunes) > 0 && len(descRunes) > 0 {
		checkPrefix := descRunes
		if len(checkPrefix) > len(titleRunes) {
			checkPrefix = checkPrefix[:len(titleRunes)]
		}
		titleSim := textSimilarity(title, string(checkPrefix))
		if titleSim > 0.9 {
			if len(descRunes) >= len(titleRunes) {
				desc = strings.TrimLeft(string(descRunes[len(titleRunes):]), " ")
			}
		}
	}
	if dynamic != "" && desc != "" {
		contentSim := textSimilarity(dynamic, desc)
		if contentSim > 0.8 {
			if len([]rune(dynamic)) < len([]rune(desc)) {
				return title, desc
			}
			return title, dynamic
		}
	}
	if dynamic != "" {
		return title, desc + VideoTextSplit + dynamic
	}
	return title, desc
}

type majorParsed struct {
	title    string
	content  string
	pics     []string
	url      string
	degraded bool
}

func parseMajor(item gjson.Result, dynID string) majorParsed {
	dynMod := item.Get("modules.module_dynamic")
	descText := decodeEscapes(strings.TrimSpace(dynMod.Get("desc.text").String()))
	major := dynMod.Get("major")

	if !major.IsObject() {
		return majorParsed{title: "", content: descText, pics: []string{}, url: ""}
	}

	majorType := major.Get("type").String()
	switch majorType {
	case "MAJOR_TYPE_ARCHIVE":
		archive := major.Get("archive")
		rawDesc := decodeEscapes(strings.TrimSpace(archive.Get("desc").String()))
		rawTitle := strings.TrimSpace(archive.Get("title").String())
		title, content := processVideoText(descText, rawDesc, rawTitle)
		var pics []string
		if c := strings.TrimSpace(archive.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   title,
			content: content,
			pics:    pics,
			url:     normalizeURL(archive.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_OPUS":
		opus := major.Get("opus")
		summary := opus.Get("summary")
		content := strings.TrimSpace(summary.Get("text").String())
		if content == "" {
			var sb strings.Builder
			for _, n := range summary.Get("rich_text_nodes").Array() {
				sb.WriteString(n.Get("text").String())
			}
			content = sb.String()
		}
		content = decodeEscapes(content)
		if content == "" {
			content = descText
		}
		var pics []string
		for _, p := range opus.Get("pics").Array() {
			if u := strings.TrimSpace(p.Get("url").String()); u != "" {
				pics = append(pics, u)
			}
		}
		return majorParsed{
			title:   strings.TrimSpace(opus.Get("title").String()),
			content: content,
			pics:    pics,
			url:     normalizeURL(opus.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_ARTICLE":
		article := major.Get("article")
		var covers []string
		for _, c := range article.Get("covers").Array() {
			if u := strings.TrimSpace(c.String()); u != "" {
				covers = append(covers, u)
			}
		}
		return majorParsed{
			title:   strings.TrimSpace(article.Get("title").String()),
			content: decodeEscapes(strings.TrimSpace(article.Get("desc").String())),
			pics:    covers,
			url:     normalizeURL(article.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_DRAW":
		draw := major.Get("draw")
		var pics []string
		for _, it := range draw.Get("items").Array() {
			if src := strings.TrimSpace(it.Get("src").String()); src != "" {
				pics = append(pics, src)
			}
		}
		return majorParsed{
			title:   "",
			content: descText,
			pics:    pics,
			url:     "",
		}

	case "MAJOR_TYPE_LIVE_RCMD":
		rcmd := major.Get("live_rcmd")
		contentRaw := strings.TrimSpace(rcmd.Get("content").String())
		if contentRaw == "" {
			return majorParsed{content: descText, degraded: true}
		}
		var contentObj map[string]any
		if err := json.Unmarshal([]byte(contentRaw), &contentObj); err != nil {
			return majorParsed{content: descText, degraded: true}
		}
		info := gjson.Get(contentRaw, "live_play_info")
		parent := strings.TrimSpace(info.Get("parent_area_name").String())
		area := strings.TrimSpace(info.Get("area_name").String())
		areaStr := strings.TrimSpace(parent + " " + area)
		var pics []string
		if c := strings.TrimSpace(info.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   strings.TrimSpace(info.Get("title").String()),
			content: areaStr,
			pics:    pics,
			url:     normalizeURL(info.Get("link").String(), true),
		}

	case "MAJOR_TYPE_LIVE":
		live := major.Get("live")
		first := strings.TrimSpace(live.Get("desc_first").String())
		second := strings.TrimSpace(live.Get("desc_second").String())
		var pics []string
		if c := strings.TrimSpace(live.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   strings.TrimSpace(live.Get("title").String()),
			content: strings.TrimSpace(first + "\n" + second),
			pics:    pics,
			url:     normalizeURL(live.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_PGC", "MAJOR_TYPE_PGC_UNION":
		pgc := major.Get("pgc")
		var pics []string
		if c := strings.TrimSpace(pgc.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   strings.TrimSpace(pgc.Get("title").String()),
			content: "",
			pics:    pics,
			url:     normalizeURL(pgc.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_COMMON":
		common := major.Get("common")
		var pics []string
		if c := strings.TrimSpace(common.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   strings.TrimSpace(common.Get("title").String()),
			content: decodeEscapes(strings.TrimSpace(common.Get("desc").String())),
			pics:    pics,
			url:     normalizeURL(common.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_COURSES":
		courses := major.Get("courses")
		sub := strings.TrimSpace(courses.Get("sub_title").String())
		desc := strings.TrimSpace(courses.Get("desc").String())
		var pics []string
		if c := strings.TrimSpace(courses.Get("cover").String()); c != "" {
			pics = append(pics, c)
		}
		return majorParsed{
			title:   strings.TrimSpace(courses.Get("title").String()),
			content: strings.TrimSpace(sub + "\n" + desc),
			pics:    pics,
			url:     normalizeURL(courses.Get("jump_url").String(), false),
		}

	case "MAJOR_TYPE_NONE":
		tips := strings.TrimSpace(major.Get("none.tips").String())
		if tips == "" {
			tips = DeletedSourceTips
		}
		return majorParsed{title: "", content: tips, pics: []string{}, url: ""}

	default:
		logging.Module("bili_dyn_sub").Warnf("动态 %s 含无法解析的 major 类型: %s", dynID, majorType)
		content := descText
		if content == "" {
			content = "无法解析的动态，类型: " + majorType
		}
		return majorParsed{title: "", content: content, pics: []string{}, url: "", degraded: true}
	}
}

func buildDynamic(item gjson.Result, dynID string, depth int) ParsedDynamic {
	dynType := item.Get("type").String()
	category := categoryMap[dynType]
	degraded := category == 0

	modules := item.Get("modules")
	author := modules.Get("module_author")
	tagText := strings.TrimSpace(modules.Get("module_tag.text").String())
	isPinned := tagText == "置顶"

	major := parseMajor(item, dynID)
	degraded = degraded || major.degraded

	var repost *ParsedDynamic
	isDeletedSource := false
	if dynType == TypeForward {
		rp, rpDegraded := parseRepost(item, dynID, depth)
		degraded = degraded || rpDegraded
		repost = rp
		if rp != nil && rp.IsDeletedSource {
			isDeletedSource = true
		}
	}

	urlStr := ""
	if dynID != "" {
		urlStr = "https://t.bilibili.com/" + dynID
	}

	return ParsedDynamic{
		DynID:           dynID,
		UID:             strings.TrimSpace(author.Get("mid").String()),
		Category:        category,
		DynType:         dynType,
		IsPinned:        isPinned,
		PubTS:           author.Get("pub_ts").Int(),
		Nickname:        strings.TrimSpace(author.Get("name").String()),
		Title:           major.title,
		Content:         major.content,
		Pics:            major.pics,
		URL:             urlStr,
		MajorURL:        major.url,
		Repost:          repost,
		IsDeletedSource: isDeletedSource,
		ParseDegraded:   degraded,
	}
}

func parseRepost(item gjson.Result, dynID string, depth int) (*ParsedDynamic, bool) {
	orig := item.Get("orig")
	if !orig.IsObject() {
		return nil, true
	}
	if depth >= MaxRepostDepth {
		return nil, true
	}

	origType := orig.Get("type").String()
	origID := strings.TrimSpace(orig.Get("id_str").String())
	origModules := orig.Get("modules")
	origMajor := origModules.Get("module_dynamic.major")

	isDeleted := origType == TypeNone || origMajor.Get("type").String() == "MAJOR_TYPE_NONE" || !origModules.Exists()
	if isDeleted {
		author := origModules.Get("module_author")
		content := parseMajor(orig, origID).content
		if content == "" {
			content = DeletedSourceTips
		}
		return &ParsedDynamic{
			DynID:           origID,
			UID:             strings.TrimSpace(author.Get("mid").String()),
			DynType:         origType,
			PubTS:           author.Get("pub_ts").Int(),
			Nickname:        strings.TrimSpace(author.Get("name").String()),
			Content:         content,
			IsDeletedSource: true,
		}, false
	}

	parsed := buildDynamic(orig, origID, depth+1)
	return &parsed, parsed.ParseDegraded
}

func ParseItem(item gjson.Result) *ParsedDynamic {
	if !item.IsObject() {
		return nil
	}
	dynID := strings.TrimSpace(item.Get("id_str").String())
	if dynID == "" {
		return nil
	}
	parsed := buildDynamic(item, dynID, 0)
	return &parsed
}

func ShouldSkip(parsed ParsedDynamic) bool {
	return skipTypes[parsed.DynType]
}

func ParseFeed(data gjson.Result) []ParsedDynamic {
	var itemsResult gjson.Result
	if data.Get("items").Exists() {
		itemsResult = data.Get("items")
	} else if data.Get("data.items").Exists() {
		itemsResult = data.Get("data.items")
	} else {
		return nil
	}

	if !itemsResult.IsArray() {
		return nil
	}

	var list []ParsedDynamic
	for _, it := range itemsResult.Array() {
		if p := ParseItem(it); p != nil {
			list = append(list, *p)
		}
	}

	sort.Slice(list, func(i, j int) bool {
		bi1, ok1 := new(big.Int).SetString(list[i].DynID, 10)
		bi2, ok2 := new(big.Int).SetString(list[j].DynID, 10)
		if ok1 && ok2 {
			return bi1.Cmp(bi2) < 0
		}
		if ok1 {
			return true
		}
		if ok2 {
			return false
		}
		return i < j
	})

	return list
}
