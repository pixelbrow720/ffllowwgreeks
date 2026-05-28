// Replay-side helpers: filesystem discovery, symbol filter, heap impl,
// and one-pass definition drain. Kept separate so main.go stays focused
// on the merge driver.

package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	dbn "github.com/NimbleMarkets/dbn-go"
	"github.com/klauspost/compress/zstd"

	"flowgreeks/internal/feed"
	"flowgreeks/internal/feed/databento"
)

// discoverFiles walks rootDir and returns every *.dbn.zst file whose
// name begins with one of the requested schemas (e.g. "tcbbo",
// "definition"). Missing dirs are tolerated — callers handle empty
// slices.
func discoverFiles(rootDir string, schemas []string) ([]string, error) {
	want := make(map[string]struct{}, len(schemas))
	for _, s := range schemas {
		want[s] = struct{}{}
	}
	var out []string
	err := filepath.WalkDir(rootDir, func(p string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			if os.IsNotExist(walkErr) {
				return filepath.SkipDir
			}
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		name := d.Name()
		if !strings.HasSuffix(name, ".dbn.zst") {
			return nil
		}
		idx := strings.Index(name, "__")
		if idx <= 0 {
			return nil
		}
		if _, ok := want[name[:idx]]; !ok {
			return nil
		}
		out = append(out, p)
		return nil
	})
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	return out, nil
}

func parseSymbolFilter(s string) (map[feed.Symbol]bool, error) {
	out := make(map[feed.Symbol]bool, 2)
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "both", "":
		out[feed.SymbolSPX] = true
		out[feed.SymbolNDX] = true
	case "spx":
		out[feed.SymbolSPX] = true
	case "ndx":
		out[feed.SymbolNDX] = true
	default:
		return nil, fmt.Errorf("invalid -symbol-filter %q (want spx|ndx|both)", s)
	}
	return out, nil
}

// drainDefinitions reads every InstrumentDefMsg in path and seeds the
// registry. Returns the number of newly-resolved instruments. Records
// outside the recognised symbol set are silently skipped (e.g. roots
// outside SPX/SPXW/NDX/NDXP/ES/NQ).
func drainDefinitions(path string, registry *databento.Registry) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()
	zr, err := zstd.NewReader(f)
	if err != nil {
		return 0, fmt.Errorf("zstd %s: %w", path, err)
	}
	defer zr.Close()
	br := bufio.NewReaderSize(zr, readBufSize)
	sc := dbn.NewDbnScanner(br)

	count := 0
	for sc.Next() {
		hdr, err := sc.GetLastHeader()
		if err != nil {
			continue
		}
		if hdr.RType != dbn.RType_InstrumentDef {
			continue
		}
		def, err := sc.DecodeInstrumentDefMsg()
		if err != nil {
			continue
		}
		raw := databento.DbnFixedString(def.RawSymbol[:])
		if registry.Resolve(def.Header.InstrumentID, raw) {
			count++
		}
	}
	if err := sc.Error(); err != nil && !errors.Is(err, io.EOF) {
		return count, fmt.Errorf("scan %s: %w", path, err)
	}
	return count, nil
}

// ─── heap.Interface on []*source ──────────────────────────────────────

type sourceHeap []*source

func (h sourceHeap) Len() int           { return len(h) }
func (h sourceHeap) Less(i, j int) bool { return h[i].nextTick.TsEvent < h[j].nextTick.TsEvent }
func (h sourceHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *sourceHeap) Push(x any)        { *h = append(*h, x.(*source)) }
func (h *sourceHeap) Pop() any {
	old := *h
	n := len(old)
	x := old[n-1]
	old[n-1] = nil
	*h = old[:n-1]
	return x
}
