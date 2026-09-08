package ai_assistant

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils/llm"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

type searchImage struct {
	URL         string `json:"url"`
	Description string `json:"description"`
}

type searchSource struct {
	Title   string
	URL     string
	Content string
	Raw     string
	Score   any
	Images  []searchImage
}

type searchPayload struct {
	Query   string
	Answer  string
	Sources []searchSource
	Images  []searchImage
}

type searchResult struct {
	Queries  []string
	Payloads []searchPayload
}

var (
	codePattern     = regexp.MustCompile("(?s)```.*?```|`[^`]*`")
	termsPattern    = regexp.MustCompile(`[A-Za-z][A-Za-z0-9_\-./]{2,}|\b\d+(?:\.\d+){1,3}\b`)
	questionPattern = regexp.MustCompile(`[^。！？!?]*[!?？][^。！？!?]*`)
	sentencePattern = regexp.MustCompile(`[。！？!?；;\n]+`)
	errorPattern    = regexp.MustCompile(`(?i)error|exception|failed|traceback|not found|无法|报错|错误`)
)

func searchText(text string) string {
	text = codePattern.ReplaceAllString(text, " ")
	lines := strings.Split(text, "\n")
	for i, line := range lines {
		lines[i] = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), ">"))
	}
	return clean(strings.Join(lines, " "))
}

func coreQuestion(text string) string {
	questions := questionPattern.FindAllString(text, -1)
	if len(questions) > 0 {
		return strings.TrimSpace(questions[len(questions)-1])
	}
	parts := sentencePattern.Split(text, -1)
	for i := len(parts) - 1; i >= 0; i-- {
		if part := strings.TrimSpace(parts[i]); part != "" {
			return part
		}
	}
	return text
}

func uniqueQueries(queries []string, cfg SearchConfig) []string {
	result := make([]string, 0, cfg.NumQueries)
	seen := make(map[string]bool)
	for _, query := range queries {
		query = clip(clean(query), cfg.QueryMaxLen)
		if query == "" || seen[query] {
			continue
		}
		seen[query] = true
		result = append(result, query)
		if len(result) == cfg.NumQueries {
			break
		}
	}
	return result
}

func (p *plugin) queries(ctx context.Context, text string, cfg Config, image bool) []string {
	if !cfg.Search.QueryRewrite {
		return uniqueQueries([]string{text}, cfg.Search)
	}
	cleaned := searchText(text)
	core := coreQuestion(cleaned)
	queries := []string{core}
	mode := "chat"
	if image {
		mode = "image"
		queries = append(queries,
			core+" 外观 服装 发色 角色设定 立绘",
			core+" official visual character design outfit appearance",
		)
	}
	terms := termsPattern.FindAllString(cleaned, -1)
	if len(terms) > 0 {
		queries = append(queries, core+" "+strings.Join(terms[:min(3, len(terms))], " "))
	}
	for _, line := range sentencePattern.Split(text, -1) {
		if errorPattern.MatchString(line) {
			queries = append(queries, line)
		}
	}
	if cfg.Search.QueryRewriteUseLLM && len([]rune(text)) >= cfg.Search.QueryRewriteLLMTriggerLen {
		prompt := fmt.Sprintf(
			"将用户输入提炼为搜索关键词，保留实体、版本、错误码。image 模式关注外观与官方视觉设定。只输出 JSON：{\"queries\":[\"关键词\"]}，最多 %d 条，每条最多 %d 字，不要省略号。",
			cfg.Search.NumQueries, cfg.Search.QueryMaxLen,
		)
		result, err := p.complete(ctx, cfg, []map[string]any{
			system(prompt),
			{"role": "user", "content": "mode=" + mode + "\nraw=" + cleaned},
		}, llm.ChatOptions{})
		var rewritten struct {
			Queries []string `json:"queries"`
		}
		if err == nil {
			err = llm.ParseJSONOutput(result.Content, &rewritten)
		}
		if err != nil {
			logging.Module(pluginID).WithError(err).Warn("查询重写失败，使用规则提取的关键词")
		} else {
			queries = append(rewritten.Queries, queries...)
		}
	}
	queries = uniqueQueries(queries, cfg.Search)
	if len(queries) == 0 {
		queries = uniqueQueries([]string{cleaned}, cfg.Search)
	}
	return queries
}

func (p *plugin) search(ctx context.Context, text string, cfg Config, mode string) (searchResult, error) {
	if optional(cfg.Search.TavilyAPIKey) == "" {
		return searchResult{}, fmt.Errorf("未配置 tavily_api_key")
	}
	image := mode == "image"
	result := searchResult{Queries: p.queries(ctx, text, cfg, image)}
	if len(result.Queries) == 0 {
		return result, fmt.Errorf("没有可用的搜索关键词")
	}
	limit := cfg.Search.ChatMaxResults
	depth := cfg.Search.ChatDepth
	answer, raw := cfg.Search.ChatIncludeAnswer, cfg.Search.ChatIncludeRawContent
	chunks, auto := cfg.Search.ChatChunksPerSource, cfg.Search.ChatAutoParameters
	switch mode {
	case "quick":
		limit, depth, raw = cfg.Search.AutoSearchQuickMaxResults, "basic", false
	case "deep":
		limit = cfg.Search.AutoSearchDeepMaxResults
	case "image":
		limit, depth = cfg.Search.ImageMaxResults, cfg.Search.ImageDepth
		answer, raw = cfg.Search.ImageIncludeAnswer, cfg.Search.ImageIncludeRawContent
		chunks, auto = cfg.Search.ImageChunksPerSource, cfg.Search.ImageAutoParameters
	}
	perQuery := int(math.Ceil(float64(limit) / float64(len(result.Queries))))
	seenURLs, seenImages := make(map[string]bool), make(map[string]bool)
	count := 0
	var lastError error
	for _, query := range result.Queries {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		body := map[string]any{
			"api_key":                    optional(cfg.Search.TavilyAPIKey),
			"query":                      query,
			"max_results":                perQuery,
			"search_depth":               depth,
			"include_answer":             answer,
			"include_raw_content":        raw,
			"include_images":             image && cfg.Search.ImageIncludeImages,
			"include_image_descriptions": image && cfg.Search.ImageIncludeImageDescriptions,
			"topic":                      "general",
		}
		if depth == "advanced" {
			body["chunks_per_source"] = max(1, min(3, chunks))
		}
		if auto {
			body["auto_parameters"] = true
		}
		payload, err := p.tavily(ctx, body, cfg.Timeout)
		if err != nil {
			lastError = err
			logging.Module(pluginID).WithError(err).Warn("一次 Tavily 查询失败")
			continue
		}
		filtered := make([]searchSource, 0, len(payload.Sources))
		for _, source := range payload.Sources {
			key := source.URL
			if key == "" {
				key = source.Title + source.Content
			}
			if seenURLs[key] || count >= limit {
				continue
			}
			seenURLs[key] = true
			filtered = append(filtered, source)
			count++
		}
		payload.Sources = filtered
		pictures := make([]searchImage, 0, len(payload.Images))
		for _, picture := range payload.Images {
			key := picture.URL + picture.Description
			if !seenImages[key] {
				seenImages[key] = true
				pictures = append(pictures, picture)
			}
		}
		payload.Images = pictures
		result.Payloads = append(result.Payloads, payload)
		if count >= limit {
			break
		}
	}
	if len(result.Payloads) == 0 {
		return result, fmt.Errorf("全部搜索请求失败：%w", lastError)
	}
	return result, nil
}

func (p *plugin) tavily(ctx context.Context, body map[string]any, timeout float64) (searchPayload, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return searchPayload{}, err
	}
	ctx, cancel := requestContext(ctx, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.tavily.com/search", bytes.NewReader(data))
	if err != nil {
		return searchPayload{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	rsp, err := p.http.Do(req)
	if err != nil {
		return searchPayload{}, err
	}
	defer rsp.Body.Close()
	if rsp.StatusCode != http.StatusOK {
		return searchPayload{}, fmt.Errorf("Tavily HTTP %d", rsp.StatusCode)
	}
	var response struct {
		Query   string           `json:"query"`
		Answer  string           `json:"answer"`
		Results []map[string]any `json:"results"`
		Images  []any            `json:"images"`
	}
	if err = json.NewDecoder(io.LimitReader(rsp.Body, 16<<20)).Decode(&response); err != nil {
		return searchPayload{}, err
	}
	payload := searchPayload{Query: response.Query, Answer: response.Answer, Images: normalizeImages(response.Images)}
	for _, item := range response.Results {
		content := stringValue(item["content"])
		if content == "" {
			content = stringValue(item["snippet"])
		}
		pictures, _ := item["images"].([]any)
		source := searchSource{
			Title:   stringValue(item["title"]),
			URL:     stringValue(item["url"]),
			Content: content,
			Raw:     stringValue(item["raw_content"]),
			Score:   item["score"],
			Images:  normalizeImages(pictures),
		}
		if source.Title != "" || source.URL != "" || source.Content != "" {
			payload.Sources = append(payload.Sources, source)
		}
	}
	return payload, nil
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text)
	}
	data, _ := json.Marshal(value)
	return string(data)
}

func normalizeImages(items []any) []searchImage {
	var result []searchImage
	seen := make(map[string]bool)
	for _, item := range items {
		var picture searchImage
		switch value := item.(type) {
		case string:
			picture.URL = value
		case map[string]any:
			for _, key := range []string{"url", "image_url"} {
				if picture.URL = stringValue(value[key]); picture.URL != "" {
					break
				}
			}
			for _, key := range []string{"description", "alt", "caption"} {
				if picture.Description = stringValue(value[key]); picture.Description != "" {
					break
				}
			}
		}
		key := picture.URL + picture.Description
		if key != "" && !seen[key] {
			seen[key] = true
			result = append(result, picture)
		}
	}
	return result
}

func evidence(result searchResult, cfg SearchConfig, mode string) string {
	var blocks []string
	answers := make(map[string]bool)
	number := 1
	for _, payload := range result.Payloads {
		if payload.Answer != "" && !answers[payload.Answer] {
			answers[payload.Answer] = true
			blocks = append(blocks, "【Tavily 摘要："+payload.Query+"】\n"+clip(clean(payload.Answer), 900))
		}
		for _, source := range payload.Sources {
			block := fmt.Sprintf("[%d] %s\n%s", number, source.Title, source.URL)
			if source.Score != nil {
				block += fmt.Sprintf("\nscore=%v", source.Score)
			}
			block += "\n摘要：" + clip(clean(source.Content), cfg.ChatContentMaxChars)
			block += "\n正文片段：" + clip(clean(source.Raw), cfg.ChatRawContentMaxChars)
			blocks = append(blocks, block)
			number++
		}
	}
	if len(blocks) == 0 {
		return ""
	}
	limit := cfg.ChatContextMaxChars
	if mode == "quick" {
		limit = cfg.AutoSearchContextMaxChars
	}
	return clip(strings.Join(blocks, "\n\n"), limit)
}
