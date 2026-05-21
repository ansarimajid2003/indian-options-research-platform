// Utility helpers for Wing-6 Live Monitor — no mock data

function ist(ts) {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}
function fmtINR(v, opts = {}) {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '−';
  const n = Math.abs(v);
  const s = n.toLocaleString('en-IN', { minimumFractionDigits: opts.dp ?? 0, maximumFractionDigits: opts.dp ?? 0 });
  return `${opts.noSign ? (v < 0 ? '−' : '') : sign}₹${s}`;
}
function fmtNum(v, dp = 2) {
  if (v == null) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function fmtPct(v, dp = 1) {
  if (v == null) return '—';
  return `${v.toFixed(dp)}%`;
}

function depthClass(ageMs) {
  if (ageMs > 4000) return 'g3';
  if (ageMs > 2000) return 'g2';
  if (ageMs > 1000) return 'g1';
  return 'g0';
}

// ── Bar aggregation ────────────────────────────────────────────────────
function aggregateBars(bars, minutes) {
  if (minutes === 1) return bars;
  if (minutes >= 99999) {
    if (!bars.length) return [];
    return [{
      time: bars[0].time,
      open: bars[0].open,
      high: Math.max(...bars.map(b => b.high)),
      low:  Math.min(...bars.map(b => b.low)),
      close: bars[bars.length - 1].close,
    }];
  }
  const out = [];
  for (let i = 0; i < bars.length; i += minutes) {
    const chunk = bars.slice(i, i + minutes);
    if (!chunk.length) continue;
    out.push({
      time:  chunk[0].time,
      open:  chunk[0].open,
      high:  Math.max(...chunk.map(b => b.high)),
      low:   Math.min(...chunk.map(b => b.low)),
      close: chunk[chunk.length - 1].close,
    });
  }
  return out;
}

// computePnL for positions panel (uses live leg data)
function computePnL(p) {
  const gross = p.unrealised_gross_pnl != null
    ? p.unrealised_gross_pnl
    : (p.current_mark != null ? (p.entry_credit || 0) - p.current_mark : null);
  const net = p.unrealised_net_pnl != null
    ? p.unrealised_net_pnl
    : (gross != null ? gross - (p.entry_charges || 0) : null);
  const current_spread = p.current_mark != null && p.entry_credit
    ? (p.current_mark / p.entry_credit - 1) * 100
    : null;
  return { gross, net, current_spread };
}

window.WingData = {
  ist, fmtINR, fmtNum, fmtPct,
  computePnL, depthClass, aggregateBars,
};
