package alive_stat

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

type stats struct {
	TotalSeconds     float64 `json:"total_seconds"`
	FirstTimestamp   float64 `json:"first_record_timestamp"`
	SessionTimestamp float64 `json:"session_start_timestamp"`
	LastSave         string  `json:"last_save_time"`
}

type botRuntime struct {
	Name, SessionTime, SessionSince, TotalTime, TotalSince string
}

type runtimeStats struct {
	mu    sync.Mutex
	path  string
	start time.Time
	first time.Time
	saved float64
}

func loadRuntime(path string) (*runtimeStats, error) {
	now := time.Now()
	r := &runtimeStats{path: path, start: now, first: now}
	var data *stats
	err := utils.ReadJSON(path, &data)
	if errors.Is(err, os.ErrNotExist) {
		return r, nil
	}
	if err != nil {
		return nil, err
	}
	if data == nil {
		return nil, fmt.Errorf("alive stats must be an object")
	}
	r.saved = data.TotalSeconds
	if data.FirstTimestamp != 0 {
		r.first = fromTimestamp(data.FirstTimestamp)
	}
	return r, nil
}

func (r *runtimeStats) save() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	data := stats{
		TotalSeconds:     r.saved + now.Sub(r.start).Seconds(),
		FirstTimestamp:   float64(r.first.UnixNano()) / 1e9,
		SessionTimestamp: float64(r.start.UnixNano()) / 1e9,
		LastSave:         now.Format("2006-01-02 15:04:05"),
	}
	encoded, err := json.MarshalIndent(data, "", "    ")
	if err != nil {
		return err
	}
	return utils.AtomicWrite(r.path, append(encoded, '\n'))
}

func (r *runtimeStats) current(now time.Time) botRuntime {
	elapsed := now.Sub(r.start).Seconds()
	return botRuntime{
		Name:         "HakuBot",
		SessionTime:  duration(elapsed),
		SessionSince: r.start.Format("Jan. 02 2006"),
		TotalTime:    duration(r.saved + elapsed),
		TotalSince:   r.first.Format("Jan. 02 2006"),
	}
}

// Autochat owns the meaning and freshness of these timestamps; only read and format them.
func autochatRuntime(path string, now time.Time) botRuntime {
	result := botRuntime{"Autochat", "N/A", "N/A", "N/A", "N/A"}
	if path == "" {
		return result
	}
	var data *stats
	if err := utils.ReadJSON(path, &data); err != nil || data == nil {
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			logging.Module("alive_stat").WithError(err).Warn("读取 Autochat 统计失败")
		}
		return result
	}
	first := now
	if data.FirstTimestamp != 0 {
		first = fromTimestamp(data.FirstTimestamp)
	}
	result.TotalTime = duration(data.TotalSeconds)
	result.TotalSince = first.Format("Jan. 02 2006")
	if data.SessionTimestamp != 0 {
		start := fromTimestamp(data.SessionTimestamp)
		result.SessionTime = duration(now.Sub(start).Seconds())
		result.SessionSince = start.Format("Jan. 02 2006")
	}
	return result
}

func fromTimestamp(seconds float64) time.Time {
	whole := int64(seconds)
	return time.Unix(whole, int64((seconds-float64(whole))*1e9)).Local()
}

func duration(seconds float64) string {
	total := int64(seconds)
	// Match Python's floor division/remainder, including negative external timestamps.
	days, rest := total/86400, total%86400
	if rest < 0 {
		days--
		rest += 86400
	}
	var parts []string
	if days > 0 {
		parts = append(parts, fmt.Sprintf("%dd", days))
	}
	if hours := rest / 3600; hours > 0 {
		parts = append(parts, fmt.Sprintf("%dh", hours))
	}
	if minutes := rest % 3600 / 60; minutes > 0 {
		parts = append(parts, fmt.Sprintf("%dmin", minutes))
	}
	parts = append(parts, fmt.Sprintf("%ds", rest%60))
	return strings.Join(parts, " ")
}
