package bilibili

import (
	"crypto/hmac"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/url"
	"strconv"
	"strings"
)

var mixinKeyEncTab = []int{
	46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
	33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
	61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
	36, 20, 34, 44, 52,
}

// GetMixinKey computes the 32-character mixin key from raw imgKey + subKey.
func GetMixinKey(orig string) string {
	if len(orig) < 64 {
		return ""
	}
	var sb strings.Builder
	for _, idx := range mixinKeyEncTab {
		if idx < len(orig) {
			sb.WriteByte(orig[idx])
		}
	}
	res := sb.String()
	if len(res) > 32 {
		return res[:32]
	}
	return res
}

// CleanWbiValue filters out characters '!', '\”, '(', ')', '*' per Bilibili WBI specification.
func CleanWbiValue(s string) string {
	var sb strings.Builder
	for _, r := range s {
		if !strings.ContainsRune("!'()*", r) {
			sb.WriteRune(r)
		}
	}
	return sb.String()
}

// ExtractKeyFromURL extracts the key from a Bilibili WBI image URL.
func ExtractKeyFromURL(rawURL string) string {
	idx := strings.LastIndex(rawURL, "/")
	if idx >= 0 {
		rawURL = rawURL[idx+1:]
	}
	dot := strings.Index(rawURL, ".")
	if dot >= 0 {
		rawURL = rawURL[:dot]
	}
	return rawURL
}

// SignQuery generates the encoded query string with wts and w_rid.
func SignQuery(params map[string]string, imgKey, subKey string, ts int64) (string, error) {
	mixinKey := GetMixinKey(imgKey + subKey)
	if mixinKey == "" {
		return "", errors.New("invalid mixin key")
	}

	vals := url.Values{}
	for k, v := range params {
		vals.Set(k, CleanWbiValue(v))
	}
	vals.Set("wts", strconv.FormatInt(ts, 10))

	query := vals.Encode()
	sum := md5.Sum([]byte(query + mixinKey))
	wRid := hex.EncodeToString(sum[:])
	return query + "&w_rid=" + wRid, nil
}

// BuildTicketParams builds query parameters for GenWebTicket.
func BuildTicketParams(ts int64) url.Values {
	tsStr := strconv.FormatInt(ts, 10)
	mac := hmac.New(sha256.New, []byte("XgwSnGZ1p"))
	mac.Write([]byte("ts" + tsStr))
	hexSign := hex.EncodeToString(mac.Sum(nil))

	q := url.Values{}
	q.Set("key_id", "ec02")
	q.Set("hexsign", hexSign)
	q.Set("context[ts]", tsStr)
	q.Set("csrf", "")
	return q
}
