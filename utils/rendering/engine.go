package rendering

import (
	"bytes"
	"context"
	"embed"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html/template"
	"os"
	"path/filepath"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils"
	mathjax "github.com/litao91/goldmark-mathjax"
	"github.com/yuin/goldmark"
	highlighting "github.com/yuin/goldmark-highlighting/v2"
	"github.com/yuin/goldmark/extension"
)

//go:embed templates/*
var templates embed.FS

func quote(v string) string { b, _ := json.Marshal(v); return string(b) }
func (b *Browser) Template(ctx context.Context, files []string, data any, funcs template.FuncMap, o Options) ([]byte, error) {
	if len(files) == 0 {
		return nil, fmt.Errorf("no template files")
	}
	t, err := template.New(filepath.Base(files[0])).Funcs(funcs).ParseFiles(files...)
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err = t.Execute(&out, data); err != nil {
		return nil, err
	}
	if o.BaseDir == "" {
		o.BaseDir = filepath.Dir(files[0])
	}
	return b.HTML(ctx, out.String(), o)
}
func (b *Browser) Text(ctx context.Context, text, css string, o Options) ([]byte, error) {
	if css == "" {
		css = "body{font-family:sans-serif;padding:20px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:20px;}"
	}
	return b.HTML(ctx, "<!doctype html><html><head><meta charset=\"utf-8\"><style>"+css+"</style></head><body>"+template.HTMLEscapeString(text)+"</body></html>", o)
}
func (b *Browser) Markdown(ctx context.Context, md, css string, o Options) ([]byte, error) {
	var out bytes.Buffer
	engine := goldmark.New(goldmark.WithExtensions(extension.GFM, highlighting.NewHighlighting(highlighting.WithStyle("github")), mathjax.NewMathJax(mathjax.WithInlineDelim(`<script type="math/tex">`, `</script>`), mathjax.WithBlockDelim(`<script type="math/tex; mode=display">`, `</script>`))))
	if err := engine.Convert([]byte(md), &out); err != nil {
		return nil, err
	}
	if css == "" {
		data, err := templates.ReadFile("templates/github-markdown-light.css")
		if err != nil {
			return nil, err
		}
		css = string(data)
	}
	var scripts strings.Builder
	if strings.Contains(out.String(), "math/tex") {
		data, err := templates.ReadFile("templates/katex/katex.min.b64_fonts.css")
		if err != nil {
			return nil, err
		}
		css += string(data)
		for _, file := range []string{"katex.min.js", "mhchem.min.js", "mathtex-script-type.min.js"} {
			data, err := templates.ReadFile("templates/katex/" + file)
			if err != nil {
				return nil, err
			}
			scripts.WriteString("<script>" + string(data) + "</script>")
		}
	}
	bodyStyle := "padding:20px;box-sizing:border-box;"
	if o.Background != "" {
		bodyStyle += "background-color:" + o.Background + ";"
	}
	footer := ""
	if o.Footer != "" {
		footer = `<div style="text-align:right;color:gray;font-size:.9em;font-style:italic;white-space:pre-wrap">` + template.HTMLEscapeString(o.Footer) + `</div>`
	}
	html := "<!doctype html><html><head><meta charset=\"utf-8\"><style>" + css + "</style></head>" +
		`<body class="markdown-body" style="` + template.HTMLEscapeString(bodyStyle) + `">` +
		out.String() + footer + scripts.String() + "</body></html>"
	return b.HTML(ctx, html, o)
}
func FontPath(paths utils.Paths, weight string) (string, error) {
	if weight == "" {
		weight = "Regular"
	}
	for _, p := range []string{filepath.Join(paths.Root, "data/shared/fonts", "SourceHanSansCN-"+weight+".ttf"), "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"} {
		if info, err := os.Stat(p); err == nil && !info.IsDir() {
			return p, nil
		}
	}
	return "", fmt.Errorf("no local CJK font for weight %s", weight)
}
func FontCSS(paths utils.Paths) (string, error) {
	path, err := FontPath(paths, "Regular")
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return "@font-face{font-family:Haku;src:url(data:font/ttf;base64," + base64.StdEncoding.EncodeToString(data) + ");}", nil
}
