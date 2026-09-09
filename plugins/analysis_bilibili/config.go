package analysis_bilibili

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"path"
	"slices"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils"
)

type Config struct {
	AnalysisWhitelist        []string `json:"analysis_whitelist"`
	AnalysisGroupWhitelist   []string `json:"analysis_group_whitelist"`
	AnalysisBlacklist        []string `json:"analysis_blacklist"`
	AnalysisGroupBlacklist   []string `json:"analysis_group_blacklist"`
	AnalysisDescBlacklist    []string `json:"analysis_desc_blacklist"`
	AnalysisTrustEnv         bool     `json:"analysis_trust_env"`
	AnalysisEnableSearch     bool     `json:"analysis_enable_search"`
	AnalysisUseOnMessage     bool     `json:"analysis_use_on_message"`
	AnalysisDisplayImage     bool     `json:"analysis_display_image"`
	AnalysisDisplayImageList []string `json:"analysis_display_image_list"`
	AnalysisImagesSize       string   `json:"analysis_images_size"`
	AnalysisCoverImagesSize  string   `json:"analysis_cover_images_size"`
	AnalysisReanalysisTime   float64  `json:"analysis_reanalysis_time"`
}

func defaultConfig() Config {
	return Config{
		AnalysisWhitelist:        []string{},
		AnalysisGroupWhitelist:   []string{},
		AnalysisBlacklist:        []string{},
		AnalysisGroupBlacklist:   []string{},
		AnalysisDescBlacklist:    []string{},
		AnalysisDisplayImageList: []string{},
	}
}

func loadConfig(path string) (Config, error) {
	cfg := defaultConfig()
	err := utils.ReadJSON(path, &cfg)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return cfg, nil
		}
		return Config{}, fmt.Errorf("读取配置失败: %w", err)
	}
	return cfg, nil
}

func (c Config) IsImageEnabled(kind string) bool {
	if c.AnalysisDisplayImage {
		return true
	}
	return slices.Contains(c.AnalysisDisplayImageList, kind)
}

func (c Config) ResizeImage(src string, isCover bool) string {
	if src == "" {
		return ""
	}
	if strings.HasPrefix(src, "//") {
		src = "https:" + src
	}
	size := c.AnalysisImagesSize
	if isCover && c.AnalysisCoverImagesSize != "" {
		size = c.AnalysisCoverImagesSize
	}
	if size == "" {
		return src
	}
	u, err := url.Parse(src)
	if err != nil {
		return src
	}
	ext := path.Ext(u.Path)
	if ext == "" {
		return src
	}
	u.Path = fmt.Sprintf("%s@%s%s", u.Path, size, ext)
	return u.String()
}
