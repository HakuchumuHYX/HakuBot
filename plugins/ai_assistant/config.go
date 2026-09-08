package ai_assistant

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/llm"
)

type ChatConfig struct {
	Provider             *string        `json:"provider"`
	APIKey               *string        `json:"api_key"`
	BaseURL              *string        `json:"base_url"`
	Model                string         `json:"model"`
	MaxTokens            *int           `json:"max_tokens"`
	ThinkingEnabled      bool           `json:"thinking_enabled"`
	ReasoningEffort      *string        `json:"reasoning_effort"`
	ExtraBody            map[string]any `json:"extra_body"`
	SystemPrompt         string         `json:"system_prompt"`
	Temperature          *float64       `json:"temperature"`
	TopP                 *float64       `json:"top_p"`
	ImageMaxSize         int            `json:"image_max_size"`
	ForwardMaxNodes      int            `json:"forward_max_nodes"`
	ForwardMaxImages     int            `json:"forward_max_images"`
	ForwardMaxTextChars  int            `json:"forward_max_text_chars"`
	ForwardIncludeImages bool           `json:"forward_include_images"`
	Watermark            string         `json:"watermark"`
	BgColor              string         `json:"bg_color"`
	RuntimeTimezone      string         `json:"runtime_timezone"`
}

type ImageConfig struct {
	Model                 string   `json:"model"`
	Size                  *string  `json:"size"`
	Quality               *string  `json:"quality"`
	PromptPrefix          string   `json:"prompt_prefix"`
	ReferenceImageMaxSize int      `json:"reference_image_max_size"`
	Timeout               *float64 `json:"timeout"`
	APIMaxRetries         int      `json:"api_max_retries"`
}

type SearchConfig struct {
	TavilyAPIKey                  *string `json:"tavily_api_key"`
	QueryRewrite                  bool    `json:"query_rewrite"`
	QueryRewriteUseLLM            bool    `json:"query_rewrite_use_llm"`
	QueryRewriteLLMTriggerLen     int     `json:"query_rewrite_llm_trigger_len"`
	QueryMaxLen                   int     `json:"query_max_len"`
	NumQueries                    int     `json:"num_queries"`
	AutoSearchEnabled             bool    `json:"auto_search_enabled"`
	AutoSearchMode                string  `json:"auto_search_mode"`
	AutoSearchQuickMaxResults     int     `json:"auto_search_quick_max_results"`
	AutoSearchDeepMaxResults      int     `json:"auto_search_deep_max_results"`
	AutoSearchContextMaxChars     int     `json:"auto_search_context_max_chars"`
	ChatMaxResults                int     `json:"chat_max_results"`
	ChatDepth                     string  `json:"chat_depth"`
	ChatChunksPerSource           int     `json:"chat_chunks_per_source"`
	ChatIncludeAnswer             any     `json:"chat_include_answer"`
	ChatIncludeRawContent         any     `json:"chat_include_raw_content"`
	ChatAutoParameters            bool    `json:"chat_auto_parameters"`
	ChatContentMaxChars           int     `json:"chat_content_max_chars"`
	ChatRawContentMaxChars        int     `json:"chat_raw_content_max_chars"`
	ChatContextMaxChars           int     `json:"chat_context_max_chars"`
	ImageMaxResults               int     `json:"image_max_results"`
	ImageDepth                    string  `json:"image_depth"`
	ImageChunksPerSource          int     `json:"image_chunks_per_source"`
	ImageIncludeAnswer            any     `json:"image_include_answer"`
	ImageIncludeRawContent        any     `json:"image_include_raw_content"`
	ImageIncludeImages            bool    `json:"image_include_images"`
	ImageIncludeImageDescriptions bool    `json:"image_include_image_descriptions"`
	ImageAutoParameters           bool    `json:"image_auto_parameters"`
	ImageVisualBriefModel         *string `json:"image_visual_brief_model"`
	ImageVisualBriefMaxTokens     int     `json:"image_visual_brief_max_tokens"`
	ImageRawContentMaxChars       int     `json:"image_raw_content_max_chars"`
	ImageContentMaxChars          int     `json:"image_content_max_chars"`
	ImageMaxReferenceImages       int     `json:"image_max_reference_images"`
}

type Config struct {
	Provider string       `json:"provider"`
	APIKey   string       `json:"api_key"`
	BaseURL  string       `json:"base_url"`
	Proxy    *string      `json:"proxy"`
	Timeout  float64      `json:"timeout"`
	Chat     ChatConfig   `json:"chat"`
	Image    ImageConfig  `json:"image"`
	Search   SearchConfig `json:"search"`
}

const defaultJSON = `{
  "provider": "openai_compatible",
  "base_url": "https://api.openai.com/v1",
  "proxy": null,
  "timeout": 60.0,
  "chat": {
    "provider": null,
    "api_key": null,
    "base_url": null,
    "model": "gpt-3.5-turbo",
    "max_tokens": 65536,
    "thinking_enabled": false,
    "reasoning_effort": null,
    "extra_body": {},
    "system_prompt": "你是HakuBot的AI助手。请遵守以下回复规范：\n1. 语气活泼、亲切、自然，像朋友聊天一样，避免生硬的机器感。\n2. 回答要充实、有内容，给出足够的细节和解释，不要过于简短或惜字如金。\n3. 如果问题涉及多个方面，请分点或分段回答，保持条理清晰。\n4. 善用 Markdown 格式（标题、列表、代码块等）来组织长回复，提升可读性。\n5. 在回答技术问题时，给出具体示例或代码片段会更好。\n6. 如果不确定答案，坦诚说明，不要编造信息。",
    "temperature": 0.7,
    "top_p": null,
    "image_max_size": 1536,
    "forward_max_nodes": 50,
    "forward_max_images": 8,
    "forward_max_text_chars": 6000,
    "forward_include_images": true,
    "watermark": "",
    "bg_color": "#f8f9fa",
    "runtime_timezone": "Asia/Shanghai"
  },
  "image": {
    "model": "gpt-image-2",
    "size": null,
    "quality": null,
    "prompt_prefix": "请根据用户需求生成或编辑图片，准确遵循用户明确指定的主体、构图、文字、配色和风格。若提供参考图，请保留与任务相关的关键视觉特征。不要擅自套用固定画风，也不要添加用户未要求的角色、文字、水印或元素。",
    "reference_image_max_size": 2048,
    "timeout": null,
    "api_max_retries": 2
  },
  "search": {
    "tavily_api_key": null,
    "query_rewrite": true,
    "query_rewrite_use_llm": true,
    "query_rewrite_llm_trigger_len": 200,
    "query_max_len": 120,
    "num_queries": 3,
    "auto_search_enabled": true,
    "auto_search_mode": "smart",
    "auto_search_quick_max_results": 3,
    "auto_search_deep_max_results": 5,
    "auto_search_context_max_chars": 3000,
    "chat_max_results": 5,
    "chat_depth": "basic",
    "chat_chunks_per_source": 1,
    "chat_include_answer": "basic",
    "chat_include_raw_content": false,
    "chat_auto_parameters": false,
    "chat_content_max_chars": 700,
    "chat_raw_content_max_chars": 1200,
    "chat_context_max_chars": 5000,
    "image_max_results": 5,
    "image_depth": "advanced",
    "image_chunks_per_source": 1,
    "image_include_answer": "basic",
    "image_include_raw_content": "text",
    "image_include_images": true,
    "image_include_image_descriptions": true,
    "image_auto_parameters": false,
    "image_visual_brief_model": null,
    "image_visual_brief_max_tokens": 65536,
    "image_raw_content_max_chars": 1200,
    "image_content_max_chars": 500,
    "image_max_reference_images": 6
  }
}`

func loadConfig(path string) (Config, error) {
	var c Config
	if err := json.Unmarshal([]byte(defaultJSON), &c); err != nil {
		return c, err
	}
	if err := utils.ReadJSON(path, &c); err != nil {
		return c, err
	}
	return c, c.validate()
}

func optional(s *string) string {
	if s == nil {
		return ""
	}
	return strings.TrimSpace(*s)
}

func (c Config) connection(image bool) (llm.Config, error) {
	result := llm.DefaultConfig()
	result.APIKey = strings.TrimSpace(c.APIKey)
	result.BaseURL = strings.TrimSpace(c.BaseURL)
	result.Proxy = optional(c.Proxy)
	result.Timeout = c.Timeout
	provider := c.Provider
	if image {
		result.Model = c.Image.Model
		result.MaxRetries = c.Image.APIMaxRetries
		if c.Image.Timeout != nil {
			result.Timeout = *c.Image.Timeout
		}
	} else {
		result.Model = c.Chat.Model
		if c.Chat.MaxTokens == nil {
			result.MaxTokens = 0
		} else {
			result.MaxTokens = *c.Chat.MaxTokens
		}
		result.ThinkingEnabled = c.Chat.ThinkingEnabled
		result.ReasoningEffort = optional(c.Chat.ReasoningEffort)
		result.ExtraBody = c.Chat.ExtraBody
		if v := optional(c.Chat.Provider); v != "" {
			provider = v
		}
		if v := optional(c.Chat.APIKey); v != "" {
			result.APIKey = v
		}
		if v := optional(c.Chat.BaseURL); v != "" {
			result.BaseURL = v
		}
	}
	provider = strings.ToLower(strings.TrimSpace(provider))
	if provider == "" {
		provider = "openai_compatible"
	}
	if result.BaseURL == "" {
		result.BaseURL = "https://api.openai.com/v1"
	}
	if provider != "openai_compatible" {
		return result, fmt.Errorf("unsupported provider %q", provider)
	}
	u, err := url.Parse(result.BaseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return result, fmt.Errorf("invalid LLM base_url")
	}
	if result.APIKey == "" || strings.TrimSpace(result.Model) == "" || result.Timeout <= 0 {
		return result, fmt.Errorf("LLM api_key/model and positive timeout are required")
	}
	return result, nil
}

func (c Config) validate() error {
	for _, image := range []bool{false, true} {
		if _, err := c.connection(image); err != nil {
			return err
		}
	}
	if _, err := time.LoadLocation(c.Chat.RuntimeTimezone); err != nil {
		return err
	}
	if c.Chat.ImageMaxSize < 0 || c.Image.ReferenceImageMaxSize < 0 || c.Chat.ForwardMaxNodes < 1 || c.Chat.ForwardMaxImages < 0 || c.Chat.ForwardMaxTextChars < 1 || c.Image.APIMaxRetries < 0 {
		return fmt.Errorf("invalid image/forward limits")
	}
	switch c.Search.AutoSearchMode {
	case "off", "smart", "always":
	default:
		return fmt.Errorf("invalid auto_search_mode")
	}
	if c.Search.NumQueries < 1 || c.Search.QueryMaxLen < 1 || c.Search.QueryRewriteLLMTriggerLen < 1 {
		return fmt.Errorf("invalid search query limits")
	}
	for _, v := range []int{c.Search.ChatMaxResults, c.Search.ImageMaxResults, c.Search.AutoSearchQuickMaxResults, c.Search.AutoSearchDeepMaxResults, c.Search.AutoSearchContextMaxChars, c.Search.ChatContextMaxChars, c.Search.ChatContentMaxChars, c.Search.ChatRawContentMaxChars, c.Search.ImageContentMaxChars, c.Search.ImageRawContentMaxChars, c.Search.ImageVisualBriefMaxTokens} {
		if v < 1 {
			return fmt.Errorf("search limits must be positive")
		}
	}
	if c.Search.ImageMaxReferenceImages < 0 {
		return fmt.Errorf("invalid image_max_reference_images")
	}
	return nil
}
