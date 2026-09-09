package bili_dyn_sub

import (
	"encoding/json"
	"errors"
	"math/big"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

var DefaultCategories = []int{1, 2, 3, 4, 5, 6}

type Subscription struct {
	Name       string  `json:"name"`
	Groups     []int64 `json:"groups"`
	Categories []int   `json:"categories"`
}

type SeenEntry struct {
	Cursor *big.Int `json:"cursor"`
	IDs    []string `json:"ids"`
}

type SubscriptionItem struct {
	UID        string  `json:"uid"`
	Name       string  `json:"name"`
	Groups     []int64 `json:"groups"`
	Categories []int   `json:"categories"`
}

type rawState struct {
	Subscriptions map[string]json.RawMessage `json:"subscriptions"`
	Seen          map[string]json.RawMessage `json:"seen"`
	LastSuccess   map[string]json.RawMessage `json:"last_success"`
}

type Store struct {
	mu            sync.RWMutex
	path          string
	subscriptions map[string]Subscription
	seen          map[string]SeenEntry
	lastSuccess   map[string]string
	cfg           *Config
	backoff       *BackoffManager
	saveMu        sync.Mutex
	saveWg        sync.WaitGroup
}

func NewStore(path string, cfg *Config, bm *BackoffManager) *Store {
	s := &Store{
		path:          path,
		subscriptions: make(map[string]Subscription),
		seen:          make(map[string]SeenEntry),
		lastSuccess:   make(map[string]string),
		cfg:           cfg,
		backoff:       bm,
	}
	s.Load()
	return s
}

func normalizeCategories(cats []int) []int {
	valid := make(map[int]bool)
	for _, c := range DefaultCategories {
		valid[c] = true
	}
	var out []int
	seen := make(map[int]bool)
	for _, c := range cats {
		if valid[c] && !seen[c] {
			seen[c] = true
			out = append(out, c)
		}
	}
	sort.Ints(out)
	if len(out) == 0 {
		return append([]int(nil), DefaultCategories...)
	}
	return out
}

func normalizeGroups(groups []int64) []int64 {
	seen := make(map[int64]bool)
	var out []int64
	for _, g := range groups {
		if g > 0 && !seen[g] {
			seen[g] = true
			out = append(out, g)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

func parseBigInt(val any) *big.Int {
	switch v := val.(type) {
	case json.Number:
		if bi, ok := new(big.Int).SetString(v.String(), 10); ok {
			return bi
		}
	case string:
		if bi, ok := new(big.Int).SetString(strings.TrimSpace(v), 10); ok {
			return bi
		}
	case float64:
		return big.NewInt(int64(v))
	case int64:
		return big.NewInt(v)
	case int:
		return big.NewInt(int64(v))
	}
	return big.NewInt(0)
}

func (s *Store) Load() {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.subscriptions = make(map[string]Subscription)
	s.seen = make(map[string]SeenEntry)
	s.lastSuccess = make(map[string]string)

	data, err := os.ReadFile(s.path)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			logging.Module("bili_dyn_sub").WithError(err).Error("读取 state.json 失败，本次以内存空状态运行")
		}
		return
	}

	var raw rawState
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.UseNumber()
	if err := dec.Decode(&raw); err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Error("解析 state.json 失败，本次以内存空状态运行")
		return
	}

	for uid, subRaw := range raw.Subscriptions {
		var item struct {
			Name       string  `json:"name"`
			Groups     []int64 `json:"groups"`
			Categories []int   `json:"categories"`
		}
		if err := json.Unmarshal(subRaw, &item); err != nil {
			logging.Module("bili_dyn_sub").Warnf("订阅条目 UID %s 不是合法对象，已跳过", uid)
			continue
		}
		normGroups := normalizeGroups(item.Groups)
		if len(normGroups) == 0 {
			logging.Module("bili_dyn_sub").Warnf("订阅条目 UID %s 没有有效群号，已跳过", uid)
			continue
		}
		s.subscriptions[uid] = Subscription{
			Name:       item.Name,
			Groups:     normGroups,
			Categories: normalizeCategories(item.Categories),
		}
	}

	for uid, seenRaw := range raw.Seen {
		var item struct {
			Cursor json.Number `json:"cursor"`
			IDs    []any       `json:"ids"`
		}
		if err := json.Unmarshal(seenRaw, &item); err != nil {
			logging.Module("bili_dyn_sub").Warnf("去重条目 UID %s 不是合法对象，已跳过", uid)
			continue
		}
		cursor := parseBigInt(item.Cursor)
		if cursor.Sign() < 0 {
			cursor = big.NewInt(0)
		}
		var ids []string
		for _, idAny := range item.IDs {
			switch idv := idAny.(type) {
			case string:
				ids = append(ids, idv)
			case json.Number:
				ids = append(ids, idv.String())
			case float64:
				ids = append(ids, strconv.FormatInt(int64(idv), 10))
			default:
				ids = append(ids, strings.TrimSpace(parseBigInt(idv).String()))
			}
		}
		s.seen[uid] = SeenEntry{
			Cursor: cursor,
			IDs:    ids,
		}
	}

	for uid, lsRaw := range raw.LastSuccess {
		var lsStr string
		if err := json.Unmarshal(lsRaw, &lsStr); err == nil && lsStr != "" {
			s.lastSuccess[uid] = lsStr
		}
	}

	logging.Module("bili_dyn_sub").Debugf("state.json 载入完成: %d 个订阅 UID, %d 份去重记录", len(s.subscriptions), len(s.seen))
}

type stateJSONOutput struct {
	Subscriptions map[string]Subscription   `json:"subscriptions"`
	Seen          map[string]seenJSONOutput `json:"seen"`
	LastSuccess   map[string]string         `json:"last_success"`
}

type seenJSONOutput struct {
	Cursor *big.Int `json:"cursor"`
	IDs    []string `json:"ids"`
}

func (s *Store) Save() error {
	s.saveMu.Lock()
	defer s.saveMu.Unlock()

	s.mu.RLock()
	out := stateJSONOutput{
		Subscriptions: make(map[string]Subscription, len(s.subscriptions)),
		Seen:          make(map[string]seenJSONOutput, len(s.seen)),
		LastSuccess:   make(map[string]string, len(s.lastSuccess)),
	}
	for uid, sub := range s.subscriptions {
		out.Subscriptions[uid] = Subscription{
			Name:       sub.Name,
			Groups:     append([]int64(nil), sub.Groups...),
			Categories: append([]int(nil), sub.Categories...),
		}
	}
	for uid, se := range s.seen {
		cursor := big.NewInt(0)
		if se.Cursor != nil {
			cursor.Set(se.Cursor)
		}
		out.Seen[uid] = seenJSONOutput{
			Cursor: cursor,
			IDs:    append([]string(nil), se.IDs...),
		}
	}
	for uid, ls := range s.lastSuccess {
		out.LastSuccess[uid] = ls
	}
	s.mu.RUnlock()

	return utils.WriteJSON(s.path, out)
}

func (s *Store) saveAsyncLocked() {
	s.saveWg.Add(1)
	go func() {
		defer s.saveWg.Done()
		if err := s.Save(); err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Error("保存 state.json 失败（仅内存生效）")
		}
	}()
}

func (s *Store) Flush() error {
	s.saveWg.Wait()
	return s.Save()
}

func (s *Store) AddSubscription(uid, name string, groupID int64, categories []int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	uid = strings.TrimSpace(uid)
	if groupID <= 0 {
		return false
	}

	sub, exists := s.subscriptions[uid]
	if !exists {
		cats := normalizeCategories(categories)
		s.subscriptions[uid] = Subscription{
			Name:       name,
			Groups:     []int64{groupID},
			Categories: cats,
		}
		s.saveAsyncLocked()
		logging.Module("bili_dyn_sub").Infof("新增订阅 UID %s name=%q group=%d", uid, name, groupID)
		return true
	}

	hasGroup := false
	for _, g := range sub.Groups {
		if g == groupID {
			hasGroup = true
			break
		}
	}
	if !hasGroup {
		sub.Groups = append(sub.Groups, groupID)
		sub.Groups = normalizeGroups(sub.Groups)
	}
	if name != "" {
		sub.Name = name
	}
	if categories != nil {
		sub.Categories = normalizeCategories(categories)
	}
	s.subscriptions[uid] = sub

	s.saveAsyncLocked()

	logging.Module("bili_dyn_sub").Infof("更新订阅 UID %s group=%d 新增=%v", uid, groupID, !hasGroup)
	return !hasGroup
}

func (s *Store) RemoveSubscription(uid string, groupID int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	uid = strings.TrimSpace(uid)
	sub, exists := s.subscriptions[uid]
	if !exists {
		return false
	}

	var newGroups []int64
	found := false
	for _, g := range sub.Groups {
		if g == groupID {
			found = true
		} else {
			newGroups = append(newGroups, g)
		}
	}
	if !found {
		return false
	}

	if len(newGroups) == 0 {
		delete(s.subscriptions, uid)
		delete(s.seen, uid)
		delete(s.lastSuccess, uid)
		if s.backoff != nil {
			s.backoff.Forget(uid)
		}
		logging.Module("bili_dyn_sub").Infof("退订 UID %s group=%d，已无订阅群，清除去重状态", uid, groupID)
	} else {
		sub.Groups = newGroups
		s.subscriptions[uid] = sub
		logging.Module("bili_dyn_sub").Infof("退订 UID %s group=%d，剩余群 %v", uid, groupID, newGroups)
	}

	s.saveAsyncLocked()
	return true
}

func (s *Store) ListSubscriptions(groupID int64) []SubscriptionItem {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var res []SubscriptionItem
	for uid, sub := range s.subscriptions {
		if groupID > 0 {
			matched := false
			for _, g := range sub.Groups {
				if g == groupID {
					matched = true
					break
				}
			}
			if !matched {
				continue
			}
		}
		res = append(res, SubscriptionItem{
			UID:        uid,
			Name:       sub.Name,
			Groups:     append([]int64(nil), sub.Groups...),
			Categories: append([]int(nil), sub.Categories...),
		})
	}
	sort.Slice(res, func(i, j int) bool { return res[i].UID < res[j].UID })
	return res
}

func (s *Store) GetAllUIDs() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()

	uids := make([]string, 0, len(s.subscriptions))
	for uid := range s.subscriptions {
		uids = append(uids, uid)
	}
	sort.Strings(uids)
	return uids
}

func (s *Store) GetGroups(uid string) []int64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	sub, ok := s.subscriptions[strings.TrimSpace(uid)]
	if !ok {
		return nil
	}
	return append([]int64(nil), sub.Groups...)
}

func (s *Store) GetCategories(uid string) []int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	sub, ok := s.subscriptions[strings.TrimSpace(uid)]
	if !ok {
		return append([]int(nil), DefaultCategories...)
	}
	return append([]int(nil), sub.Categories...)
}

func (s *Store) GetName(uid string) string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.subscriptions[strings.TrimSpace(uid)].Name
}

func (s *Store) IsSubscribed(uid string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	_, ok := s.subscriptions[strings.TrimSpace(uid)]
	return ok
}

func (s *Store) IsBaselineInitialized(uid string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	entry, ok := s.seen[strings.TrimSpace(uid)]
	return ok && entry.Cursor != nil && entry.Cursor.Sign() > 0
}

func (s *Store) InitBaseline(uid string, ids []string, cursor *big.Int) {
	s.mu.Lock()
	defer s.mu.Unlock()

	uid = strings.TrimSpace(uid)
	limit := s.cfg.SeenIDsMax
	if limit < 1 {
		limit = 50
	}

	numericMap := make(map[string]*big.Int)
	var strIDs []string
	idSeen := make(map[string]bool)
	for _, id := range ids {
		trimmed := strings.TrimSpace(id)
		if trimmed != "" && !idSeen[trimmed] {
			idSeen[trimmed] = true
			strIDs = append(strIDs, trimmed)
			if bi, ok := new(big.Int).SetString(trimmed, 10); ok {
				numericMap[trimmed] = bi
			}
		}
	}

	sort.Slice(strIDs, func(i, j int) bool {
		bi1, ok1 := numericMap[strIDs[i]]
		bi2, ok2 := numericMap[strIDs[j]]
		if ok1 && ok2 {
			return bi1.Cmp(bi2) < 0
		}
		if ok1 {
			return true
		}
		if ok2 {
			return false
		}
		return strIDs[i] < strIDs[j]
	})

	if len(strIDs) > limit {
		strIDs = strIDs[len(strIDs)-limit:]
	}

	finalCursor := big.NewInt(0)
	if cursor != nil && cursor.Sign() > 0 {
		finalCursor.Set(cursor)
	}
	for _, bi := range numericMap {
		if bi.Cmp(finalCursor) > 0 {
			finalCursor.Set(bi)
		}
	}

	s.seen[uid] = SeenEntry{
		Cursor: finalCursor,
		IDs:    strIDs,
	}

	s.saveAsyncLocked()

	logging.Module("bili_dyn_sub").Infof("建立基线 UID %s cursor=%s ids=%d 条", uid, finalCursor.String(), len(strIDs))
}

func (s *Store) IsSeen(uid, dynID string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()

	uid = strings.TrimSpace(uid)
	entry, ok := s.seen[uid]
	if !ok {
		return false
	}
	dynID = strings.TrimSpace(dynID)
	for _, id := range entry.IDs {
		if id == dynID {
			return true
		}
	}
	if bi, ok := new(big.Int).SetString(dynID, 10); ok {
		if entry.Cursor != nil && entry.Cursor.Sign() > 0 && bi.Cmp(entry.Cursor) <= 0 {
			return true
		}
	}
	return false
}

func (s *Store) MarkSeen(uid, dynID string, save bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	uid = strings.TrimSpace(uid)
	dynID = strings.TrimSpace(dynID)
	entry, ok := s.seen[uid]
	if !ok {
		entry = SeenEntry{Cursor: big.NewInt(0), IDs: []string{}}
	}

	found := false
	for _, id := range entry.IDs {
		if id == dynID {
			found = true
			break
		}
	}
	if !found {
		entry.IDs = append(entry.IDs, dynID)
	}

	limit := s.cfg.SeenIDsMax
	if limit < 1 {
		limit = 50
	}
	if len(entry.IDs) > limit {
		entry.IDs = entry.IDs[len(entry.IDs)-limit:]
	}

	if bi, ok := new(big.Int).SetString(dynID, 10); ok {
		if entry.Cursor == nil || bi.Cmp(entry.Cursor) > 0 {
			entry.Cursor = bi
		}
	} else {
		logging.Module("bili_dyn_sub").Warnf("动态 ID 非纯数字，仅记录到 seen_ids: UID=%s id=%q", uid, dynID)
	}
	s.seen[uid] = entry

	if save {
		s.saveAsyncLocked()
	}
}

func (s *Store) TouchLastSuccess(uid string, save bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	uid = strings.TrimSpace(uid)
	s.lastSuccess[uid] = time.Now().Format("2006-01-02T15:04:05")
	if save {
		s.saveAsyncLocked()
	}
}

func (s *Store) GetLastSuccess(uid string) string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lastSuccess[strings.TrimSpace(uid)]
}

func (s *Store) Prune() int {
	s.mu.Lock()
	defer s.mu.Unlock()

	limit := s.cfg.SeenIDsMax
	if limit < 1 {
		limit = 50
	}
	retentionDays := s.cfg.SeenRetentionDays
	if retentionDays < 1 {
		retentionDays = 14
	}
	cutoff := time.Now().AddDate(0, 0, -retentionDays)

	trimmedIDs := 0
	droppedUIDs := 0

	for uid, entry := range s.seen {
		if _, subscribed := s.subscriptions[uid]; !subscribed {
			lastStr := s.lastSuccess[uid]
			stale := true
			if lastStr != "" {
				if t, err := time.ParseInLocation("2006-01-02T15:04:05", lastStr, time.Local); err == nil {
					stale = t.Before(cutoff)
				}
			}
			if stale {
				delete(s.seen, uid)
				delete(s.lastSuccess, uid)
				droppedUIDs++
				continue
			}
		}

		if len(entry.IDs) > limit {
			trimmedIDs += len(entry.IDs) - limit
			entry.IDs = entry.IDs[len(entry.IDs)-limit:]
			s.seen[uid] = entry
		}
	}

	for uid := range s.lastSuccess {
		if _, inSub := s.subscriptions[uid]; !inSub {
			if _, inSeen := s.seen[uid]; !inSeen {
				delete(s.lastSuccess, uid)
			}
		}
	}

	totalRemoved := trimmedIDs + droppedUIDs
	if totalRemoved > 0 {
		s.saveAsyncLocked()
		logging.Module("bili_dyn_sub").Infof("裁剪去重状态: 截断 %d 条 ID，清除 %d 个失效 UID 状态", trimmedIDs, droppedUIDs)
	}
	return totalRemoved
}
