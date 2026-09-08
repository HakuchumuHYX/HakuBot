package rendering

import (
	"bytes"
	"html/template"
	"strings"

	chromahtml "github.com/alecthomas/chroma/v2/formatters/html"
	mathjax "github.com/litao91/goldmark-mathjax"
	"github.com/yuin/goldmark"
	highlighting "github.com/yuin/goldmark-highlighting/v2"
	"github.com/yuin/goldmark/extension"
	"github.com/yuin/goldmark/util"
)

type MarkdownDocument struct {
	HTML    template.HTML
	MathCSS template.CSS
	Scripts []template.JS
}

// MarkdownContent converts content and returns its math assets; the caller selects the page template.
func MarkdownContent(md string) (MarkdownDocument, error) {
	var out bytes.Buffer
	engine := goldmark.New(goldmark.WithExtensions(
		extension.GFM,
		highlighting.NewHighlighting(
			highlighting.WithFormatOptions(chromahtml.WithClasses(true)),
			highlighting.WithWrapperRenderer(codeWrapper),
		),
		mathjax.NewMathJax(
			mathjax.WithInlineDelim(`<script type="math/tex">`, `</script>`),
			mathjax.WithBlockDelim(`<script type="math/tex; mode=display">`, `</script>`),
		),
	))
	if err := engine.Convert([]byte(md), &out); err != nil {
		return MarkdownDocument{}, err
	}
	result := MarkdownDocument{HTML: template.HTML(out.String())}
	if strings.Contains(out.String(), "math/tex") {
		css, err := ReadAsset("katex/katex.min.b64_fonts.css")
		if err != nil {
			return MarkdownDocument{}, err
		}
		result.MathCSS = template.CSS(css)
		for _, name := range []string{"katex.min.js", "mhchem.min.js", "mathtex-script-type.min.js"} {
			data, err := ReadAsset("katex/" + name)
			if err != nil {
				return MarkdownDocument{}, err
			}
			result.Scripts = append(result.Scripts, template.JS(data))
		}
	}
	return result, nil
}

func codeWrapper(w util.BufWriter, _ highlighting.CodeBlockContext, entering bool) {
	if entering {
		w.WriteString(`<div class="codehilite">`)
	} else {
		w.WriteString(`</div>`)
	}
}
