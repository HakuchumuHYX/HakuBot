package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/network"
	jsonrepair "github.com/RealAlexandreAI/json-repair"
)

type Config struct {
	APIKey          string         `json:"api_key"`
	BaseURL         string         `json:"base_url"`
	Model           string         `json:"model"`
	Timeout         float64        `json:"timeout"`
	Proxy           string         `json:"proxy"`
	MaxRetries      int            `json:"max_retries"`
	MaxTokens       int            `json:"max_tokens"`
	ThinkingEnabled bool           `json:"thinking_enabled"`
	ReasoningEffort string         `json:"reasoning_effort"`
	ExtraBody       map[string]any `json:"extra_body"`
}

func DefaultConfig() Config { return Config{Timeout: 60, MaxRetries: 2, MaxTokens: 65536} }

type Client struct {
	config Config
	http   *http.Client
}

func New(config Config) (*Client, error) {
	if config.APIKey == "" || config.BaseURL == "" || config.Model == "" {
		return nil, errors.New("LLM api_key, base_url and model are required")
	}
	c, err := network.NewClient(config.Proxy, time.Duration(config.Timeout*float64(time.Second)), false)
	if err != nil {
		return nil, err
	}
	return &Client{config, c}, nil
}
func (c *Client) Close() error { c.http.CloseIdleConnections(); return nil }

type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}
type ChatResult struct {
	Content, Model string
	Usage          Usage
	Elapsed        time.Duration
}
type ChatOptions struct {
	Model                     string
	MaxTokens                 *int
	Temperature, TopP         *float64
	ThinkingEnabled           *bool
	ReasoningEffort           string
	ExtraBody, ResponseFormat map[string]any
}

func (c *Client) Chat(ctx context.Context, messages []map[string]any, o ChatOptions) (ChatResult, error) {
	body := maps.Clone(c.config.ExtraBody)
	if body == nil {
		body = map[string]any{}
	}
	maps.Copy(body, o.ExtraBody)
	model := o.Model
	if model == "" {
		model = c.config.Model
	}
	body["model"] = model
	body["messages"] = messages
	tokens := c.config.MaxTokens
	if o.MaxTokens != nil {
		tokens = *o.MaxTokens
	}
	if tokens > 0 {
		body["max_tokens"] = tokens
	}
	thinking := c.config.ThinkingEnabled
	if o.ThinkingEnabled != nil {
		thinking = *o.ThinkingEnabled
	}
	if thinking {
		body["thinking"] = map[string]string{"type": "enabled"}
	} else {
		if o.Temperature != nil {
			body["temperature"] = *o.Temperature
		}
		if o.TopP != nil {
			body["top_p"] = *o.TopP
		}
	}
	effort := o.ReasoningEffort
	if effort == "" {
		effort = c.config.ReasoningEffort
	}
	if effort != "" {
		body["reasoning_effort"] = effort
	}
	if o.ResponseFormat != nil {
		body["response_format"] = o.ResponseFormat
	}
	data, err := json.Marshal(body)
	if err != nil {
		return ChatResult{}, err
	}
	var result struct {
		Choices []struct {
			Message struct {
				Content json.RawMessage `json:"content"`
			} `json:"message"`
		} `json:"choices"`
		Usage Usage `json:"usage"`
	}
	start := time.Now()
	err = c.request(ctx, "chat/completions", "application/json", data, &result)
	if err != nil {
		return ChatResult{}, err
	}
	content := ""
	if len(result.Choices) > 0 {
		raw := result.Choices[0].Message.Content
		if json.Unmarshal(raw, &content) != nil {
			var parts []struct {
				Text string `json:"text"`
			}
			if err = json.Unmarshal(raw, &parts); err != nil {
				return ChatResult{}, err
			}
			var texts []string
			for _, p := range parts {
				if p.Text != "" {
					texts = append(texts, p.Text)
				}
			}
			content = strings.Join(texts, "\n")
		}
	}
	return ChatResult{content, model, result.Usage, time.Since(start)}, nil
}

type ImageOptions struct{ Model, Size, Quality string }
type Image struct {
	Filename, MIME string
	Data           []byte
}
type imageResponse struct {
	Data []struct {
		URL    string `json:"url"`
		Base64 string `json:"b64_json"`
	} `json:"data"`
}

func imageResult(r imageResponse) (string, error) {
	if len(r.Data) == 0 {
		return "", errors.New("image API returned no images")
	}
	if r.Data[0].Base64 != "" {
		return "base64://" + r.Data[0].Base64, nil
	}
	if r.Data[0].URL != "" {
		return r.Data[0].URL, nil
	}
	return "", errors.New("image API returned no URL or image bytes")
}
func (c *Client) imageFields(prompt string, o ImageOptions) map[string]any {
	model := o.Model
	if model == "" {
		model = c.config.Model
	}
	fields := map[string]any{"model": model, "prompt": prompt, "n": 1}
	if o.Size != "" {
		fields["size"] = o.Size
	}
	if o.Quality != "" {
		fields["quality"] = o.Quality
	}
	return fields
}
func (c *Client) GenerateImage(ctx context.Context, prompt string, o ImageOptions) (string, error) {
	data, err := json.Marshal(c.imageFields(prompt, o))
	if err != nil {
		return "", err
	}
	var r imageResponse
	if err = c.request(ctx, "images/generations", "application/json", data, &r); err != nil {
		return "", err
	}
	return imageResult(r)
}
func (c *Client) EditImage(ctx context.Context, prompt string, images []Image, o ImageOptions) (string, error) {
	if len(images) == 0 {
		return "", errors.New("at least one image is required")
	}
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	for k, v := range c.imageFields(prompt, o) {
		if err := w.WriteField(k, fmt.Sprint(v)); err != nil {
			return "", err
		}
	}
	for _, img := range images {
		field := "image"
		if len(images) > 1 {
			field = "image[]"
		}
		h := make(textproto.MIMEHeader)
		h.Set("Content-Disposition", fmt.Sprintf(`form-data; name=%q; filename=%q`, field, img.Filename))
		h.Set("Content-Type", img.MIME)
		part, err := w.CreatePart(h)
		if err != nil {
			return "", err
		}
		if _, err = part.Write(img.Data); err != nil {
			return "", err
		}
	}
	if err := w.Close(); err != nil {
		return "", err
	}
	var r imageResponse
	if err := c.request(ctx, "images/edits", w.FormDataContentType(), b.Bytes(), &r); err != nil {
		return "", err
	}
	return imageResult(r)
}
func (c *Client) request(ctx context.Context, path, contentType string, data []byte, out any) error {
	for attempt := 0; ; attempt++ {
		err := func() error {
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(c.config.BaseURL, "/")+"/"+path, bytes.NewReader(data))
			if err != nil {
				return err
			}
			req.Header.Set("Authorization", "Bearer "+c.config.APIKey)
			req.Header.Set("Content-Type", contentType)
			rsp, err := c.http.Do(req)
			if err != nil {
				return err
			}
			defer rsp.Body.Close()
			if rsp.StatusCode < 200 || rsp.StatusCode >= 300 {
				return &network.HTTPError{Status: rsp.StatusCode}
			}
			return json.NewDecoder(io.LimitReader(rsp.Body, 128<<20)).Decode(out)
		}()
		if err == nil {
			return nil
		}
		if attempt >= c.config.MaxRetries || !network.Retryable(err) {
			return err
		}
		if err = network.Wait(ctx, time.Second*time.Duration(1<<min(attempt, 5))); err != nil {
			return err
		}
	}
}

// ParseJSONOutput accepts plain JSON and fenced JSON without silently inventing fields.
func ParseJSONOutput(text string, out any) error {
	text = strings.TrimSpace(text)
	if strings.HasPrefix(text, "```") {
		if i := strings.IndexByte(text, '\n'); i >= 0 {
			text = text[i+1:]
		}
		text = strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(text), "```"))
	}
	return json.Unmarshal([]byte(text), out)
}

// ParseJSON uses repair only when the caller explicitly opts in.
func ParseJSON(text string, out any, repair bool) error {
	if err := ParseJSONOutput(text, out); err == nil {
		return nil
	} else if !repair {
		return err
	}
	fixed, err := jsonrepair.RepairJSON(text)
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(fixed), out)
}
