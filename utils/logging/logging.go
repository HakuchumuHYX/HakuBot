package logging

import (
	"bytes"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"

	"github.com/sirupsen/logrus"
	zero "github.com/wdvxdr1123/ZeroBot"
)

// Init configures the global logger also used by ZeroBot. Call before starting workers.
func Init(output io.Writer, level logrus.Level, color bool) {
	logrus.SetOutput(output)
	logrus.SetLevel(level)
	logrus.SetFormatter(&formatter{color: color})
}

func Module(name string) *logrus.Entry {
	return logrus.WithField("module", name)
}

// Event attaches IDs without copying message contents into every business log.
func Event(module string, event *zero.Event) *logrus.Entry {
	log := Module(module)
	if event == nil {
		return log
	}
	fields := logrus.Fields{"bot_id": event.SelfID}
	if event.GroupID != 0 {
		fields["group_id"] = event.GroupID
	}
	if event.UserID != 0 {
		fields["user_id"] = event.UserID
	}
	if event.MessageID != nil {
		fields["message_id"] = event.MessageID
	}
	return log.WithFields(fields)
}

type formatter struct{ color bool }

func (f *formatter) Format(entry *logrus.Entry) ([]byte, error) {
	var b bytes.Buffer
	level := strings.ToUpper(entry.Level.String())
	if entry.Level == logrus.WarnLevel {
		level = "WARN"
	}
	level = fmt.Sprintf("%-5s", level)
	if f.color {
		code := "36"
		switch entry.Level {
		case logrus.PanicLevel, logrus.FatalLevel, logrus.ErrorLevel:
			code = "31"
		case logrus.WarnLevel:
			code = "33"
		case logrus.InfoLevel:
			code = "32"
		}
		level = "\x1b[" + code + "m" + level + "\x1b[0m"
	}
	module := "zerobot"
	if name, ok := entry.Data["module"]; ok {
		module = fmt.Sprint(name)
	}
	message := strings.TrimRight(entry.Message, "\r\n")
	message = strings.ReplaceAll(message, "\n", "\n    ")
	fmt.Fprintf(&b, "%s %s [%s] %s", entry.Time.Local().Format("2006-01-02 15:04:05"), level, module, message)
	keys := make([]string, 0, len(entry.Data))
	for key := range entry.Data {
		if key != "module" {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	for _, key := range keys {
		value := fmt.Sprint(entry.Data[key])
		if value == "" || strings.ContainsAny(value, " \t\r\n\"=\\") {
			value = strconv.Quote(value)
		}
		fmt.Fprintf(&b, " %s=%s", key, value)
	}
	b.WriteByte('\n')
	return b.Bytes(), nil
}
