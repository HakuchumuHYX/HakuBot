package rendering

import (
	"bytes"
	"context"
	"embed"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html/template"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/HakuchumuHYX/HakuBot/utils"
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

// TemplateFS renders an embedded or directory-backed template without selecting its design.
func (b *Browser) TemplateFS(ctx context.Context, files fs.FS, name string, data any, o Options) ([]byte, error) {
	t, err := template.ParseFS(files, name)
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err = t.Execute(&out, data); err != nil {
		return nil, err
	}
	return b.HTML(ctx, out.String(), o)
}

func ReadAsset(name string) ([]byte, error) {
	return templates.ReadFile("templates/" + name)
}

func (b *Browser) Text(ctx context.Context, text, css string, o Options) ([]byte, error) {
	if css == "" {
		data, err := ReadAsset("text.css")
		if err != nil {
			return nil, err
		}
		css = string(data)
	}
	return b.TemplateFS(ctx, templates, "templates/text.html", struct {
		Text string
		CSS  template.CSS
	}{text, template.CSS(css)}, o)
}

func (b *Browser) Markdown(ctx context.Context, md, css string, o Options) ([]byte, error) {
	content, err := MarkdownContent(md)
	if err != nil {
		return nil, err
	}
	if css == "" {
		for _, name := range []string{"github-markdown-light.css", "pygments-default.css"} {
			data, err := ReadAsset(name)
			if err != nil {
				return nil, err
			}
			css += string(data) + "\n"
		}
	}
	return b.TemplateFS(ctx, templates, "templates/markdown.html", struct {
		Content MarkdownDocument
		CSS     template.CSS
	}{content, template.CSS(css)}, o)
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
