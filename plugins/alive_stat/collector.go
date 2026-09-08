package alive_stat

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/mem"
	"github.com/shirou/gopsutil/v4/process"
)

type resource struct {
	Name        string
	Percent     float64
	Used, Total uint64
	Available   bool
}

type processInfo struct {
	Name        string
	Status      string
	Memory      uint64
	MemoryKnown bool
}

type networkResult struct {
	Host    string
	Status  string
	Latency *float64
}

type serverStatus struct {
	Hostname, CPUModel, Uptime string
	Cores                      int
	Resources                  []resource
	Processes                  []processInfo
	Network                    []networkResult
}

func collect(ctx context.Context, cfg config) serverStatus {
	result := serverStatus{
		Hostname: "N/A", CPUModel: "N/A", Uptime: "N/A",
		Resources: []resource{{Name: "CPU"}, {Name: "Mem"}, {Name: "Swap"}, {Name: "Disk"}},
		Processes: make([]processInfo, len(cfg.MonitoredProcesses)+len(cfg.DockerProcesses)),
		Network:   make([]networkResult, len(cfg.PingHosts)),
	}
	var tasks sync.WaitGroup
	run := func(work func()) {
		tasks.Add(1)
		go func() {
			defer tasks.Done()
			work()
		}()
	}
	run(func() {
		if name, err := os.Hostname(); err == nil {
			result.Hostname = name
		} else {
			report("hostname", err)
		}
		if info, err := cpu.InfoWithContext(ctx); err == nil && len(info) > 0 {
			result.CPUModel = info[0].ModelName
		} else {
			report("CPU model", err)
		}
		if count, err := cpu.CountsWithContext(ctx, true); err == nil {
			result.Cores = count
		} else {
			report("CPU cores", err)
		}
		if uptime, err := host.UptimeWithContext(ctx); err == nil {
			result.Uptime = duration(float64(uptime))
		} else {
			report("system uptime", err)
		}
	})
	run(func() {
		values, err := cpu.PercentWithContext(ctx, 500*time.Millisecond, false)
		if err == nil && len(values) > 0 {
			result.Resources[0].Percent, result.Resources[0].Available = values[0], true
		} else {
			report("CPU usage", err)
		}
	})
	run(func() {
		if value, err := mem.VirtualMemoryWithContext(ctx); err == nil {
			percent := 0.0
			if value.Total > 0 {
				percent = float64(value.Total-min(value.Total, value.Available)) / float64(value.Total) * 100
			}
			// Preserve psutil's used bytes while its percentage is based on available memory.
			used := value.Total - min(value.Total, value.Free+value.Buffers+value.Cached)
			result.Resources[1] = resource{"Mem", percent, used, value.Total, true}
		} else {
			report("memory", err)
		}
		if value, err := mem.SwapMemoryWithContext(ctx); err == nil {
			result.Resources[2] = resource{"Swap", value.UsedPercent, value.Used, value.Total, true}
		} else {
			report("swap", err)
		}
		if value, err := disk.UsageWithContext(ctx, "/"); err == nil {
			result.Resources[3] = resource{"Disk", value.UsedPercent, value.Used, value.Total, true}
		} else {
			report("disk", err)
		}
	})
	run(func() { copy(result.Processes, collectProcesses(ctx, cfg.MonitoredProcesses)) })
	for i, item := range cfg.DockerProcesses {
		run(func() { result.Processes[len(cfg.MonitoredProcesses)+i] = dockerProcess(ctx, item) })
	}
	for i, name := range cfg.PingHosts {
		run(func() { result.Network[i] = pingHost(ctx, name) })
	}
	tasks.Wait()
	return result
}

func report(part string, err error) {
	if err != nil && !errors.Is(err, context.Canceled) {
		logging.Module("alive_stat").WithField("collector", part).WithError(err).Warn("采集不可用")
	}
}

type processSnapshot struct {
	pid, parent int32
	command     string
	memory      uint64
	memoryKnown bool
}

func collectProcesses(ctx context.Context, entries []processEntry) []processInfo {
	result := make([]processInfo, len(entries))
	for i, entry := range entries {
		result[i] = processInfo{Name: entry.Name, Status: "Stopped"}
	}
	if len(entries) == 0 {
		return result
	}
	all, err := process.ProcessesWithContext(ctx)
	if err != nil {
		report("processes", err)
		for i := range result {
			result[i].Status = "Unknown"
		}
		return result
	}
	rows := make(map[int32]processSnapshot)
	children := make(map[int32][]int32)
	incomplete := false
	for _, proc := range all {
		if ctx.Err() != nil {
			return result
		}
		parent, parentErr := proc.PpidWithContext(ctx)
		command, commandErr := proc.CmdlineWithContext(ctx)
		if errors.Is(parentErr, os.ErrNotExist) || errors.Is(commandErr, os.ErrNotExist) {
			continue
		}
		if parentErr != nil || commandErr != nil {
			incomplete = true
		}
		row := processSnapshot{pid: proc.Pid, parent: parent, command: strings.ToLower(command)}
		if usage, err := proc.MemoryInfoWithContext(ctx); err == nil {
			row.memory, row.memoryKnown = usage.RSS, true
		}
		rows[row.pid] = row
		children[parent] = append(children[parent], row.pid)
	}
	counted := make(map[int32]bool)
	for i, entry := range entries {
		var roots []int32
		for pid, row := range rows {
			if counted[pid] {
				continue
			}
			for _, keyword := range strings.Split(entry.Keyword, "|") {
				keyword = strings.ToLower(strings.TrimSpace(keyword))
				if keyword != "" && strings.Contains(row.command, keyword) {
					roots = append(roots, pid)
					break
				}
			}
		}
		if len(roots) == 0 {
			if incomplete {
				result[i].Status = "Unknown"
			}
			continue
		}
		result[i].Status, result[i].MemoryKnown = "Running", true
		for len(roots) > 0 {
			pid := roots[len(roots)-1]
			roots = roots[:len(roots)-1]
			if counted[pid] {
				continue
			}
			counted[pid] = true
			row := rows[pid]
			result[i].Memory += row.memory
			result[i].MemoryKnown = result[i].MemoryKnown && row.memoryKnown
			roots = append(roots, children[pid]...)
		}
	}
	if incomplete {
		report("processes", fmt.Errorf("some process metadata could not be read"))
	}
	return result
}

func commandOutput(ctx context.Context, timeout time.Duration, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), "LC_ALL=C")
	output, err := cmd.Output()
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	return output, err
}

func dockerProcess(ctx context.Context, entry dockerEntry) processInfo {
	result := processInfo{Name: entry.Name, Status: "Unknown"}
	output, err := commandOutput(ctx, 5*time.Second, "docker", "inspect", "--format", "{{.State.Running}}", entry.Container)
	if err != nil {
		report(entry.Name, err)
		return result
	}
	switch strings.TrimSpace(string(output)) {
	case "false":
		result.Status = "Stopped"
		return result
	case "true":
		result.Status = "Running"
	default:
		report(entry.Name, fmt.Errorf("unexpected Docker running state"))
		return result
	}
	output, err = commandOutput(ctx, 5*time.Second, "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", entry.Container)
	if err != nil {
		report(entry.Name, err)
		return result
	}
	value := strings.TrimSpace(strings.SplitN(string(output), "/", 2)[0])
	result.Memory, err = dockerBytes(value)
	result.MemoryKnown = err == nil
	report(entry.Name, err)
	return result
}

var memoryPattern = regexp.MustCompile(`^([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB|kB|KB|MB|GB|TB)$`)

func dockerBytes(value string) (uint64, error) {
	match := memoryPattern.FindStringSubmatch(value)
	if match == nil {
		return 0, fmt.Errorf("invalid Docker memory value")
	}
	amount, err := strconv.ParseFloat(match[1], 64)
	if err != nil {
		return 0, err
	}
	units := map[string]float64{"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}
	return uint64(amount * units[match[2]]), nil
}

var latencyPattern = regexp.MustCompile(`time[=<]([0-9]+(?:\.[0-9]+)?)`)

func pingHost(ctx context.Context, name string) networkResult {
	result := networkResult{Host: name, Status: "Unknown"}
	output, err := commandOutput(ctx, 5*time.Second, "ping", "-c", "1", "-W", "3", "--", name)
	if err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() == 1 {
			result.Status = "timeout"
		} else {
			report("ping", err)
		}
		return result
	}
	result.Status = "ok"
	if match := latencyPattern.FindSubmatch(output); match != nil {
		if value, err := strconv.ParseFloat(string(match[1]), 64); err == nil {
			result.Latency = &value
		}
	}
	return result
}

func formatBytes(value uint64) string {
	switch {
	case value < 1024:
		return fmt.Sprintf("%dB", value)
	case value < 1<<20:
		return fmt.Sprintf("%.1fK", float64(value)/(1<<10))
	case value < 1<<30:
		return fmt.Sprintf("%.1fM", float64(value)/(1<<20))
	default:
		return fmt.Sprintf("%.1fG", float64(value)/(1<<30))
	}
}
