// Mock data + simulated WebSocket frame generator for Wing-6 Live Monitor

const SESSION_DATE = '2026-05-11';
const MARKET_OPEN_MS = (() => { const d = new Date(); d.setHours(9, 20, 0, 0); return d.getTime(); })();
const MARKET_CLOSE_MS = (() => { const d = new Date(); d.setHours(15, 20, 0, 0); return d.getTime(); })();

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

// ── Initial positions ──────────────────────────────────────────────────
const INITIAL_POSITIONS = [
  {
    symbol: 'NIFTY', expiry: '2026-05-14',
    lots: 4, lot_size: 75,
    entry_time: '09:21:14',
    entry_credit: 142.85, entry_charges: 187.20,
    short_ce_strike: 25200, long_ce_strike: 25400,
    short_pe_strike: 24700, long_pe_strike: 24500,
    short_ce_premium: 78.40, long_ce_premium: 22.10,
    short_pe_premium: 96.55, long_pe_premium: 32.20,
    current_mark: 138.20,
    leg_ages: [820, 410, 1100, 640],
  },
  {
    symbol: 'FINNIFTY', expiry: '2026-05-13',
    lots: 4, lot_size: 25,
    entry_time: '09:22:48',
    entry_credit: 168.90, entry_charges: 142.40,
    short_ce_strike: 23800, long_ce_strike: 24000,
    short_pe_strike: 23200, long_pe_strike: 23000,
    short_ce_premium: 94.20, long_ce_premium: 31.80,
    short_pe_premium: 102.40, long_pe_premium: 38.90,
    current_mark: 154.10,
    leg_ages: [320, 280, 540, 380],
  },
  {
    symbol: 'MIDCPNIFTY', expiry: '2026-05-12',
    lots: 4, lot_size: 50,
    entry_time: '09:24:02',
    entry_credit: 88.40, entry_charges: 156.80,
    short_ce_strike: 12450, long_ce_strike: 12550,
    short_pe_strike: 12100, long_pe_strike: 12000,
    short_ce_premium: 48.20, long_ce_premium: 18.40,
    short_pe_premium: 52.10, long_pe_premium: 22.10,
    current_mark: 96.80,
    leg_ages: [1240, 950, 4820, 1180],
  },
  {
    symbol: 'SENSEX', expiry: '2026-05-13',
    lots: 4, lot_size: 10,
    entry_time: '09:25:33',
    entry_credit: 245.20, entry_charges: 218.40,
    short_ce_strike: 82800, long_ce_strike: 83100,
    short_pe_strike: 81600, long_pe_strike: 81300,
    short_ce_premium: 138.20, long_ce_premium: 48.10,
    short_pe_premium: 168.50, long_pe_premium: 61.40,
    current_mark: 218.40,
    leg_ages: [620, 410, 780, 540],
  },
];

function computePnL(p) {
  const totalLotSize = p.lots * p.lot_size;
  const gross = (p.entry_credit - p.current_mark) * totalLotSize;
  const net = gross - p.entry_charges;
  const current_spread = ((p.current_mark / p.entry_credit) - 1) * 100;
  return { gross, net, current_spread };
}

// ── Equity curve seed ──────────────────────────────────────────────────
function generateEquitySeed() {
  const pts = [];
  // Anchor a synthetic session ending "now" so the curve always populates
  // regardless of the viewer's local clock. ~3.4h of history, 15s bars.
  const endTime = Date.now();
  const startTime = endTime - 3.4 * 3600 * 1000;
  let cum_gross = 0, cum_net = 0;
  for (let t = startTime; t <= endTime; t += 15000) {
    const elapsed = (t - startTime) / 60000;
    // an interesting curve: up early, dip mid-morning, recovery
    const trend = 80 * Math.log(1 + elapsed / 30) - 35 * Math.sin(elapsed / 28) + (Math.random() - 0.5) * 18;
    cum_gross = Math.round(trend * 95 + elapsed * 11);
    cum_net = cum_gross - Math.floor(elapsed * 2.4 + 220);
    pts.push({
      time: Math.floor(t / 1000),
      cumulative_gross_pnl: cum_gross,
      cumulative_net_pnl: cum_net,
    });
  }
  return pts;
}

// ── Signal log ─────────────────────────────────────────────────────────
const SIGNAL_LOG_SEED = [
  { ts: '09:20:02', event: 'skip', symbol: 'BANKNIFTY', reason: 'vix_above_band', vix: 18.42, dte: 2, bucket: 'A' },
  { ts: '09:21:14', event: 'entry', symbol: 'NIFTY', reason: 'wing6_4x1_filled', vix: 14.20, dte: 3, bucket: 'B' },
  { ts: '09:22:48', event: 'entry', symbol: 'FINNIFTY', reason: 'wing6_4x1_filled', vix: 14.18, dte: 2, bucket: 'B' },
  { ts: '09:23:11', event: 'skip', symbol: 'NIFTY', reason: 'depth_insufficient', vix: 14.15, dte: 3, bucket: 'B' },
  { ts: '09:24:02', event: 'entry', symbol: 'MIDCPNIFTY', reason: 'wing6_4x1_filled', vix: 14.10, dte: 1, bucket: 'C' },
  { ts: '09:25:33', event: 'entry', symbol: 'SENSEX', reason: 'wing6_4x1_filled', vix: 14.08, dte: 2, bucket: 'B' },
  { ts: '09:48:21', event: 'skip', symbol: 'NIFTY', reason: 'spread_widened_5pct', vix: 14.62, dte: 3, bucket: 'B' },
  { ts: '10:14:08', event: 'skip', symbol: 'FINNIFTY', reason: 'quote_stale_long_call', vix: 14.45, dte: 2, bucket: 'B' },
  { ts: '10:42:11', event: 'skip', symbol: 'MIDCPNIFTY', reason: 'partial_entry_blocked', vix: 14.38, dte: 1, bucket: 'C' },
  { ts: '11:08:55', event: 'skip', symbol: 'NIFTY', reason: 'bucket_filter_C_excluded', vix: 14.51, dte: 3, bucket: 'C' },
  { ts: '11:34:02', event: 'skip', symbol: 'SENSEX', reason: 'iv_skew_outside_band', vix: 14.40, dte: 2, bucket: 'B' },
  { ts: '12:01:42', event: 'skip', symbol: 'NIFTY', reason: 'reentry_cooldown_22m', vix: 14.32, dte: 3, bucket: 'B' },
  { ts: '12:14:28', event: 'skip', symbol: 'FINNIFTY', reason: 'depth_insufficient', vix: 14.28, dte: 2, bucket: 'B' },
];

// ── Depth health ───────────────────────────────────────────────────────
const DEPTH_HEALTH_SEED = [
  { symbol: 'NIFTY',      legs: [{age: 820, qty: 8400}, {age: 410, qty: 12200}, {age: 1100, qty: 7600}, {age: 640, qty: 9800}] },
  { symbol: 'FINNIFTY',   legs: [{age: 320, qty: 4200}, {age: 280, qty: 5800}, {age: 540, qty: 4800}, {age: 380, qty: 5100}] },
  { symbol: 'MIDCPNIFTY', legs: [{age: 1240, qty: 2400}, {age: 950, qty: 1800}, {age: 4820, qty: 850}, {age: 1180, qty: 2100}] },
  { symbol: 'SENSEX',     legs: [{age: 620, qty: 1450}, {age: 410, qty: 2100}, {age: 780, qty: 1380}, {age: 540, qty: 1820}] },
];

function depthClass(ageMs) {
  if (ageMs > 4000) return 'g3';
  if (ageMs > 2000) return 'g2';
  if (ageMs > 1000) return 'g1';
  return 'g0';
}

// ── Alerts ─────────────────────────────────────────────────────────────
const ALERTS_SEED = [
  { ts: '12:24:18', severity: 'warning', component: 'depth_collector', reason: 'snapshot_stale',     message: 'MIDCPNIFTY long_put depth age 4.8s exceeds 4s threshold', delivery: 'sent', delivery_ts: '12:24:20' },
  { ts: '12:01:42', severity: 'info',    component: 'engine',           reason: 'reentry_cooldown',  message: 'NIFTY reentry blocked — 22m remaining in cooldown window',  delivery: 'sent', delivery_ts: '12:01:44' },
  { ts: '11:08:32', severity: 'warning', component: 'feed',             reason: 'feed_reconnect',    message: 'Dhan feed reconnected after 2.1s disconnect (seq gap=0)',     delivery: 'sent', delivery_ts: '11:08:34' },
  { ts: '10:42:11', severity: 'warning', component: 'engine',           reason: 'partial_entry_blocked', message: 'MIDCPNIFTY 4x1 only 3/4 legs fillable — order rejected',  delivery: 'sent', delivery_ts: '10:42:13' },
  { ts: '09:20:00', severity: 'info',    component: 'engine',           reason: 'session_open',      message: 'Wing-6 monitoring active · profile=wing6_4x1_all_vix_filtered', delivery: 'sent', delivery_ts: '09:20:01' },
];

// ── Mock LivePushFrame generator ───────────────────────────────────────
function generateFrame(state) {
  // jitter the marks
  state.positions.forEach(p => {
    const drift = (Math.random() - 0.495) * 0.6;
    p.current_mark = Math.max(0, p.current_mark + drift);
    p.leg_ages = p.leg_ages.map(a => Math.max(60, Math.min(8000, a + (Math.random() - 0.45) * 220)));
  });
  // depth jitter
  state.depth.forEach(d => {
    d.legs.forEach(leg => {
      leg.age = Math.max(60, Math.min(8000, leg.age + (Math.random() - 0.45) * 250));
      leg.qty = Math.max(100, leg.qty + Math.floor((Math.random() - 0.5) * 800));
    });
  });
  // equity new tick
  const lastEq = state.equity[state.equity.length - 1];
  const t = lastEq.time + 5;
  const next_gross = lastEq.cumulative_gross_pnl + Math.floor((Math.random() - 0.45) * 95);
  const next_net = next_gross - Math.floor(state.equity.length * 0.04 + 240);
  state.equity.push({ time: t, cumulative_gross_pnl: next_gross, cumulative_net_pnl: next_net });
  return { ts: new Date().toISOString(), equity_tick: { time: t, value: next_net } };
}

// ── Spot chart seed (1-min bars from 09:15 to now) ────────────────────
const SPOT_CONFIG = {
  NIFTY:      { base: 24950, vol: 28, step: 50  },
  FINNIFTY:   { base: 23480, vol: 35, step: 50  },
  MIDCPNIFTY: { base: 12220, vol: 22, step: 25  },
  SENSEX:     { base: 82100, vol: 95, step: 100 },
};

function generateSpotSeed(symbol) {
  const cfg = SPOT_CONFIG[symbol];
  const bars = [];
  const endTime = Date.now();
  const startTime = endTime - 3.4 * 3600 * 1000;
  let close = cfg.base;
  for (let t = startTime; t <= endTime; t += 60000) {
    const drift = (Math.random() - 0.494) * cfg.vol;
    const open = close;
    close = Math.max(cfg.base * 0.96, Math.min(cfg.base * 1.04, close + drift));
    const high = Math.max(open, close) + Math.random() * cfg.vol * 0.4;
    const low  = Math.min(open, close) - Math.random() * cfg.vol * 0.4;
    bars.push({ time: Math.floor(t / 1000), open: +open.toFixed(2), high: +high.toFixed(2), low: +low.toFixed(2), close: +close.toFixed(2) });
  }
  return bars;
}

// ── Option chain seed ──────────────────────────────────────────────────
function generateOptionChain(symbol) {
  const cfg = SPOT_CONFIG[symbol];
  const spot = cfg.base + (Math.random() - 0.5) * cfg.vol * 2;
  const atmStrike = Math.round(spot / cfg.step) * cfg.step;
  const rows = [];
  for (let i = -10; i <= 10; i++) {
    const strike = atmStrike + i * cfg.step;
    const dist = Math.abs(strike - spot);
    const moneyness = dist / spot;
    // CE side (higher strikes = OTM for CE)
    const ce_iv = 14.2 + moneyness * 180 + (Math.random() - 0.5) * 1.2;
    const ce_delta = Math.max(0.01, 0.5 - moneyness * 4.5 - (i > 0 ? i * 0.04 : 0));
    const ce_ltp  = Math.max(0.05, (strike > spot ? 0 : (spot - strike)) + Math.exp(-moneyness * 6) * cfg.base * 0.012);
    const ce_bid  = Math.max(0.05, ce_ltp - cfg.vol * 0.08);
    const ce_ask  = ce_ltp + cfg.vol * 0.08;
    const ce_oi   = Math.floor((1 - moneyness * 3) * 850000 + Math.random() * 120000);
    const ce_oi_chg = Math.floor((Math.random() - 0.42) * 80000);
    const ce_vol  = Math.floor(ce_oi * 0.12 + Math.random() * 20000);
    // PE side (lower strikes = OTM for PE)
    const pe_iv   = 14.2 + moneyness * 180 + (Math.random() - 0.5) * 1.2;
    const pe_delta = Math.max(0.01, 0.5 - moneyness * 4.5 + (i < 0 ? i * 0.04 : 0));
    const pe_ltp  = Math.max(0.05, (strike < spot ? 0 : (strike - spot)) + Math.exp(-moneyness * 6) * cfg.base * 0.012);
    const pe_bid  = Math.max(0.05, pe_ltp - cfg.vol * 0.08);
    const pe_ask  = pe_ltp + cfg.vol * 0.08;
    const pe_oi   = Math.floor((1 - moneyness * 3) * 820000 + Math.random() * 110000);
    const pe_oi_chg = Math.floor((Math.random() - 0.5) * 75000);
    const pe_vol  = Math.floor(pe_oi * 0.11 + Math.random() * 18000);
    rows.push({
      strike, atm: strike === atmStrike,
      itm_ce: strike < spot, itm_pe: strike > spot,
      ce: { iv: ce_iv, delta: ce_delta, ltp: ce_ltp, bid: ce_bid, ask: ce_ask, oi: Math.max(0, ce_oi), oi_chg: ce_oi_chg, vol: ce_vol },
      pe: { iv: pe_iv, delta: pe_delta, ltp: pe_ltp, bid: pe_bid, ask: pe_ask, oi: Math.max(0, pe_oi), oi_chg: pe_oi_chg, vol: pe_vol },
    });
  }
  return { spot: +spot.toFixed(2), atmStrike, rows };
}

window.WingData = {
  ist, fmtINR, fmtNum, fmtPct,
  INITIAL_POSITIONS, computePnL,
  generateEquitySeed,
  SIGNAL_LOG_SEED,
  DEPTH_HEALTH_SEED, depthClass,
  ALERTS_SEED,
  generateFrame,
  SPOT_CONFIG, generateSpotSeed, generateOptionChain,
};


// ─────────────

// Shared UI primitives (icons, badges, sparkline)

const { useEffect, useRef, useState, useMemo } = React;

function Icon({ name, size = 12, color = 'currentColor' }) {
  const props = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: color, strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round', className: 'icon' };
  switch (name) {
    case 'search':  return <svg {...props}><circle cx="7" cy="7" r="4.5"/><path d="M11 11l3 3"/></svg>;
    case 'filter':  return <svg {...props}><path d="M2 3h12M4 7.5h8M6 12h4"/></svg>;
    case 'expand':  return <svg {...props}><path d="M3 6V3h3M13 6V3h-3M3 10v3h3M13 10v3h-3"/></svg>;
    case 'export':  return <svg {...props}><path d="M8 2v8M5 7l3 3 3-3M3 13h10"/></svg>;
    case 'pause':   return <svg {...props}><rect x="4" y="3" width="3" height="10"/><rect x="9" y="3" width="3" height="10"/></svg>;
    case 'play':    return <svg {...props}><path d="M4 3l8 5-8 5z" fill={color}/></svg>;
    case 'lock':    return <svg {...props}><rect x="3.5" y="7" width="9" height="6.5"/><path d="M5 7V5a3 3 0 116 0v2"/></svg>;
    case 'speaker': return <svg {...props}><path d="M3 6v4h2l3 2.5v-9L5 6H3z"/><path d="M11 5.5c1 .6 1.5 1.6 1.5 2.5s-.5 1.9-1.5 2.5"/></svg>;
    case 'check':   return <svg {...props}><path d="M3 8l3 3 7-7"/></svg>;
    case 'dot':     return <svg {...props}><circle cx="8" cy="8" r="2.5" fill={color} stroke="none"/></svg>;
    case 'wave':    return <svg {...props}><path d="M2 8c1-2 2-2 3 0s2 2 3 0 2-2 3 0 2 2 3 0"/></svg>;
    case 'gear':    return <svg {...props}><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M15 8h-2M3 8H1M12.95 3.05l-1.4 1.4M4.45 11.55l-1.4 1.4M12.95 12.95l-1.4-1.4M4.45 4.45l-1.4-1.4"/></svg>;
    default: return null;
  }
}

function MarketBadge({ status }) {
  const cls = status === 'OPEN' ? 'open' : status === 'PRE_OPEN' ? 'preopen' : status === 'HOLIDAY' ? 'holiday' : 'closed';
  return <span className={`badge ${cls}`}><span className="dot" />{status}</span>;
}

function Sparkline({ data, width = 110, height = 22, color = 'oklch(0.78 0.18 150)' }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - 2 - ((v - min) / range) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={width} height={height} className="spark">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.2" />
    </svg>
  );
}

function Meter({ value, max = 100, warnAt = 85, critAt = 70 }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const cls = value < critAt ? 'crit' : value < warnAt ? 'warn' : '';
  return <div className="meter"><div className={`meter-fill ${cls}`} style={{ width: `${pct}%` }} /></div>;
}

function useTicker(intervalMs = 1000) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return tick;
}

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

Object.assign(window, { Icon, MarketBadge, Sparkline, Meter, useTicker, useClock });


// ─────────────

// All Live Monitor panels


// ── Positions panel ────────────────────────────────────────────────────
function PositionsPanel({ positions }) {
  return (
    <div className="panel span-positions">
      <div className="panel-header">
        <div className="panel-title">
          Open Positions
          <span className="count">{positions.length} · 4×1 IRON CONDOR</span>
        </div>
        <div className="panel-actions">
          <span className="badge ghost"><Icon name="dot" size={10} color="oklch(0.78 0.18 150)"/> SYNCED · 1s</span>
          <div className="icon-btn"><Icon name="filter" size={11}/></div>
          <div className="icon-btn"><Icon name="export" size={11}/></div>
          <div className="icon-btn"><Icon name="expand" size={11}/></div>
        </div>
      </div>
      <div className="panel-body" style={{overflowX: 'auto'}}>
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">Symbol</th>
              <th className="l">Short CE / Long CE</th>
              <th className="l">Short PE / Long PE</th>
              <th>Entry</th>
              <th>Entry ₹</th>
              <th>Mark ₹</th>
              <th>Spread</th>
              <th>Unrealised Gross</th>
              <th>Unrealised Net</th>
              <th>Leg Ages (ms)</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => {
              const { gross, net, current_spread } = computePnL(p);
              const cls = net >= 0 ? 'pnl-pos' : 'pnl-neg';
              return (
                <tr key={p.symbol} className={`pos-row ${cls}`}>
                  <td className="l">
                    <div className="sym-cell">
                      <span className="sym">{p.symbol}</span>
                      <span className="exp">{p.expiry} · {p.lots}×{p.lot_size}</span>
                    </div>
                  </td>
                  <td className="l">
                    <span className="legpair">
                      <span className="short">{p.short_ce_strike}CE</span>
                      <span className="sep">/</span>
                      <span className="long">{p.long_ce_strike}CE</span>
                    </span>
                  </td>
                  <td className="l">
                    <span className="legpair">
                      <span className="short">{p.short_pe_strike}PE</span>
                      <span className="sep">/</span>
                      <span className="long">{p.long_pe_strike}PE</span>
                    </span>
                  </td>
                  <td className="muted">{p.entry_time}</td>
                  <td>{fmtNum(p.entry_credit)}</td>
                  <td>{fmtNum(p.current_mark)}</td>
                  <td className={current_spread > 0 ? 'neg' : 'pos'}>{current_spread > 0 ? '+' : ''}{current_spread.toFixed(2)}%</td>
                  <td className={gross >= 0 ? 'pos' : 'neg'}>{fmtINR(gross)}</td>
                  <td className={net >= 0 ? 'pos' : 'neg'} style={{fontWeight: 600}}>{fmtINR(net)}</td>
                  <td>
                    <span style={{display:'inline-flex', gap:3}}>
                      {p.leg_ages.map((a, i) => (
                        <span key={i} className={`age-chip ${a > 4000 ? 'crit' : a > 2000 ? 'warn' : ''}`}>{Math.round(a)}</span>
                      ))}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Equity curve ───────────────────────────────────────────────────────
function EquityCurvePanel({ seriesRef, equityState, accentColor = '#4ade80' }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const netSeriesRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !window.LightweightCharts) return;
    const chart = window.LightweightCharts.createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#111111' }, textColor: '#a3a3a3', fontFamily: 'JetBrains Mono', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1, vertLine: { color: 'rgba(255,255,255,0.2)', width: 1, style: 3 }, horzLine: { color: 'rgba(255,255,255,0.2)', width: 1, style: 3 } },
      handleScale: true, handleScroll: true,
    });
    const series = chart.addAreaSeries({
      lineColor: '#4ade80',
      topColor: 'rgba(74,222,128,0.28)',
      bottomColor: 'rgba(74,222,128,0.0)',
      lineWidth: 1.6,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });
    chartRef.current = chart;
    netSeriesRef.current = series;
    seriesRef.current = series;
    const chartData = equityState.map(p => ({ time: p.time, value: p.cumulative_net_pnl }));
    series.setData(chartData);
    setTimeout(() => chart.timeScale().fitContent(), 80);
    return () => { chart.remove(); };
  }, []);

  // Update series colour when accentColor tweak changes
  useEffect(() => {
    if (!netSeriesRef.current) return;
    // parse hex to rgba for fill
    const hex = accentColor.replace('#', '');
    const r = parseInt(hex.slice(0,2),16), g = parseInt(hex.slice(2,4),16), b = parseInt(hex.slice(4,6),16);
    netSeriesRef.current.applyOptions({
      lineColor: accentColor,
      topColor: `rgba(${r},${g},${b},0.28)`,
      bottomColor: `rgba(${r},${g},${b},0.0)`,
    });
  }, [accentColor]);

  const last = equityState[equityState.length - 1] || {};
  const first = equityState[0] || {};
  const high = Math.max(...equityState.map(p => p.cumulative_net_pnl));
  return (
    <div className="panel span-equity">
      <div className="panel-header">
        <div className="panel-title">Intraday Equity Curve <span className="count">NET P&L · 09:20–15:20</span></div>
        <div className="panel-actions">
          <span className="badge ok"><span className="dot"/>LIVE</span>
          <div className="icon-btn"><Icon name="expand" size={11}/></div>
        </div>
      </div>
      <div className="eq-summary">
        <div>
          <div className="lbl">Net P&L</div>
          <div className={`val ${last.cumulative_net_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtINR(last.cumulative_net_pnl)}</div>
          <div className="delta muted">peak {fmtINR(high)}</div>
        </div>
        <div>
          <div className="lbl">Gross P&L</div>
          <div className={`val ${last.cumulative_gross_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtINR(last.cumulative_gross_pnl)}</div>
          <div className="delta muted">charges {fmtINR(last.cumulative_gross_pnl - last.cumulative_net_pnl, {noSign:true})}</div>
        </div>
      </div>
      <div ref={containerRef} className="eq-chart" />
    </div>
  );
}

// ── Signal log ─────────────────────────────────────────────────────────
function SignalLogPanel({ entries }) {
  return (
    <div className="panel span-alerts">
      <div className="panel-header">
        <div className="panel-title">Signal Log <span className="count">{entries.length} EVENTS · LIVE</span></div>
        <div className="panel-actions">
          <span className="badge ghost">SKIP {entries.filter(e=>e.event==='skip').length}</span>
          <span className="badge ok">ENTRY {entries.filter(e=>e.event==='entry').length}</span>
          <div className="icon-btn"><Icon name="filter" size={11}/></div>
        </div>
      </div>
      <div className="panel-body sig-list" style={{maxHeight: 280, overflowY:'auto'}}>
        {entries.map((e, i) => (
          <div className="sig-row" key={i}>
            <span className="ts">{e.ts}</span>
            <span className={`ev ${e.event}`}>{e.event === 'entry' ? 'E' : e.event === 'exit' ? 'X' : 'S'}</span>
            <span className="sym">{e.symbol}</span>
            <span className="reason">{e.reason}</span>
            <span className="meta">vix {e.vix?.toFixed(2)} · dte {e.dte} · bkt {e.bucket}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Depth Health ───────────────────────────────────────────────────────
function DepthHealthPanel({ depth }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Depth Health <span className="count">4 LEGS · 246 CHANNELS</span></div>
        <div className="panel-actions">
          <span className="badge ghost">AGE · QTY</span>
        </div>
      </div>
      <div className="panel-body">
        <div className="heat">
          <div className="heat-head">
            <div></div>
            <div>short CE</div>
            <div>long CE</div>
            <div>short PE</div>
            <div>long PE</div>
          </div>
          {depth.map(d => (
            <div className="heat-row" key={d.symbol}>
              <div className="sym">{d.symbol}</div>
              {d.legs.map((leg, i) => (
                <div key={i} className={`heat-cell ${depthClass(leg.age)}`}>
                  <span className="age">{(leg.age/1000).toFixed(1)}s</span>
                  <span className="qty">{leg.qty.toLocaleString('en-IN')}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div style={{padding:'8px 14px 12px', display:'flex', gap:14, fontSize:10, color:'var(--text-3)', borderTop:'1px solid var(--border)'}}>
          <span style={{display:'flex', alignItems:'center', gap:5}}><span style={{width:8,height:8,background:'var(--green-soft)', border:'1px solid var(--green-line)'}}/>&lt;1s</span>
          <span style={{display:'flex', alignItems:'center', gap:5}}><span style={{width:8,height:8,background:'oklch(0.78 0.18 150 / 0.07)', border:'1px solid var(--border-strong)'}}/>1–2s</span>
          <span style={{display:'flex', alignItems:'center', gap:5}}><span style={{width:8,height:8,background:'oklch(0.82 0.15 80 / 0.10)', border:'1px solid oklch(0.82 0.15 80 / 0.35)'}}/>2–4s warn</span>
          <span style={{display:'flex', alignItems:'center', gap:5}}><span style={{width:8,height:8,background:'var(--red-soft)', border:'1px solid var(--red-line)'}}/>&gt;4s stale</span>
        </div>
      </div>
    </div>
  );
}

// ── Storage / writer health ────────────────────────────────────────────
function StoragePanel({ wdHistory }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Storage · Writer Health <span className="count">WD MOUNT OK</span></div>
        <div className="panel-actions">
          <span className="badge ok"><span className="dot"/>FLUSHING</span>
        </div>
      </div>
      <div className="panel-body">
        <div className="stor-grid">
          <div className="stor-cell">
            <span className="lbl">Raw Packet Flush Age</span>
            <span className="val pos">0.42s</span>
            <span className="sub">/wd/raw/2026-05-11/ · 14:32:18</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">Parquet Flush Age</span>
            <span className="val">3.81s</span>
            <span className="sub">/wd/parquet/order-book/ · 14:32:14</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">WD Free Space</span>
            <span className="val">1.84 TB</span>
            <div style={{marginTop:2}}>
              <Sparkline data={wdHistory} width={200} height={22} color="oklch(0.78 0.12 220)"/>
            </div>
            <span className="sub muted">−18.4 GB over 3h · 6.2 d runway</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">Backpressure</span>
            <span className="val pos">CLEAR</span>
            <span className="sub">queue depth 4 / 8192 · 0 drops</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Alerts timeline ────────────────────────────────────────────────────
function AlertsPanel({ alerts }) {
  const counts = alerts.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc; }, {});
  const lastSent = alerts.find(a => a.delivery === 'sent');
  return (
    <div className="panel span-alerts">
      <div className="panel-header">
        <div className="panel-title">Alert Timeline <span className="count">{alerts.length} TODAY · TELEGRAM</span></div>
        <div className="panel-actions">
          <span className="badge ghost">UPTIME 99.94%</span>
          <span className="badge ok">RECOVERED 2</span>
        </div>
      </div>
      <div className="panel-body">
        <div className="alert-summary">
          <div><div className="lbl">Critical</div><div className="val neg">{counts.critical || 0}</div></div>
          <div><div className="lbl">Warning</div><div className="val" style={{color:'var(--amber)'}}>{counts.warning || 0}</div></div>
          <div><div className="lbl">Info</div><div className="val" style={{color:'var(--cyan)'}}>{counts.info || 0}</div></div>
          <div><div className="lbl">Last Telegram</div><div className="val" style={{fontSize:14}}>{lastSent?.delivery_ts || '—'}</div><div className="muted" style={{fontSize:10}}>delta 1.8s</div></div>
          <div><div className="lbl">External Heartbeat</div><div className="val pos" style={{fontSize:14}}>OK · 14:32:09</div><div className="muted" style={{fontSize:10}}>healthchecks.io</div></div>
        </div>
        <div className="timeline">
          {alerts.map((a, i) => (
            <div className="alert-row" key={i}>
              <span className="ts">{a.ts}</span>
              <span className={`badge ${a.severity}`}>{a.severity.toUpperCase()}</span>
              <span className="comp">{a.component}</span>
              <span className="msg">{a.message}</span>
              <span className={`delivery ${a.delivery==='sent' ? 'sent' : ''}`}>
                <Icon name="check" size={10}/> tg · {a.delivery_ts}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Bar aggregation ────────────────────────────────────────────────────
function aggregateBars(bars, minutes) {
  if (minutes === 1) return bars;
  if (minutes >= 99999) {
    // 1D — collapse all to one bar
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

const TF_OPTIONS = [
  { label: '1m',  minutes: 1 },
  { label: '5m',  minutes: 5 },
  { label: '15m', minutes: 15 },
  { label: '1H',  minutes: 60 },
  { label: '1D',  minutes: 99999 },
];

// ── Mini spot chart (per instrument) ──────────────────────────────────
function SpotChartCard({ symbol, bars }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [tf, setTf] = useState(1);

  useEffect(() => {
    if (!containerRef.current || !window.LightweightCharts || !bars.length) return;
    const chart = window.LightweightCharts.createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#111111' }, textColor: '#6e6e6e', fontFamily: 'JetBrains Mono', fontSize: 9 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.025)' }, horzLines: { color: 'rgba(255,255,255,0.025)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)', scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      handleScale: true, handleScroll: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#4ade80', downColor: '#f87171',
      borderUpColor: '#4ade80', borderDownColor: '#f87171',
      wickUpColor: 'rgba(74,222,128,0.5)', wickDownColor: 'rgba(248,113,113,0.5)',
    });
    series.setData(aggregateBars(bars, 1));
    chartRef.current = chart;
    seriesRef.current = series;
    setTimeout(() => chart.timeScale().fitContent(), 80);
    return () => chart.remove();
  }, []);

  // Update data when TF changes
  useEffect(() => {
    if (!seriesRef.current || !bars.length) return;
    const tfOpt = TF_OPTIONS.find(t => t.minutes === tf);
    seriesRef.current.setData(aggregateBars(bars, tfOpt.minutes));
    setTimeout(() => chartRef.current?.timeScale().fitContent(), 50);
  }, [tf]);

  const last  = bars[bars.length - 1] || {};
  const first = bars[0] || {};
  const chg    = last.close - first.open;
  const chgPct = (chg / first.open) * 100;

  return (
    <div className="panel" style={{gridColumn: 'span 3'}}>
      <div className="spot-header-bar">
        <div style={{display:'flex', alignItems:'center', justifyContent:'space-between'}}>
          <span className="spot-sym">{symbol} <span style={{color:'var(--text-3)', fontWeight:400, fontSize:10}}>SPOT</span></span>
          <div className="tf-bar">
            {TF_OPTIONS.map(t => (
              <div key={t.label} className={`tf-btn ${tf === t.minutes ? 'active' : ''}`} onClick={() => setTf(t.minutes)}>
                {t.label}
              </div>
            ))}
          </div>
        </div>
        <div className="spot-price-row">
          <span className={`spot-price ${chg >= 0 ? 'pos' : 'neg'}`}>{last.close?.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
          <span className={`spot-change ${chg >= 0 ? 'pos' : 'neg'}`}>{chg >= 0 ? '+' : ''}{chg.toFixed(2)} ({chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%)</span>
        </div>
        <span className="spot-meta">H {last.high?.toFixed(2)} · L {last.low?.toFixed(2)}</span>
      </div>
      <div ref={containerRef} className="spot-chart-container" />
    </div>
  );
}

function SpotChartsRow({ spotData }) {
  return (
    <>
      {['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'].map(sym => (
        <SpotChartCard key={sym} symbol={sym} bars={spotData[sym] || []} />
      ))}
    </>
  );
}

// ── Option chain panel ─────────────────────────────────────────────────
const OI_MAX = 900000;
function OIBar({ value, color }) {
  const pct = Math.min(100, (value / OI_MAX) * 100);
  return (
    <div className="oi-bar">
      <span>{value.toLocaleString('en-IN')}</span>
      <div className="bar"><div className="bar-fill" style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

function OptionChainPanel({ chainData }) {
  const symbols = ['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'];
  const [active, setActive] = useState('NIFTY');
  const chain = chainData[active] || { rows: [], spot: 0 };

  return (
    <div className="panel span-alerts">
      <div className="panel-header">
        <div className="panel-title">
          Option Chain
          <span className="count">NSE · LIVE · ATM ± 10 STRIKES</span>
        </div>
        <div className="panel-actions">
          <span className="badge ghost">EXP NEAREST</span>
          <span className="badge ok"><span className="dot"/>QUOTE LIVE</span>
          <div className="icon-btn"><Icon name="export" size={11}/></div>
        </div>
      </div>
      <div className="chain-tabs">
        {symbols.map(s => (
          <div key={s} className={`chain-tab ${active === s ? 'active' : ''}`} onClick={() => setActive(s)}>
            {s}
            <span className="spot-chip">{chainData[s]?.spot?.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
          </div>
        ))}
        <div style={{flex:1, borderBottom:'none'}} />
        <div style={{padding:'9px 14px', fontSize:10, color:'var(--text-3)', fontFamily:'JetBrains Mono, monospace', display:'flex', alignItems:'center', gap:10}}>
          <span>ATM <strong style={{color:'var(--text)'}}>{chain.atmStrike}</strong></span>
          <span>·</span>
          <span>Spot <strong style={{color:'var(--text)'}}>{chain.spot?.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}</strong></span>
        </div>
      </div>
      <div className="chain-table-wrap">
        <table className="chain-tbl">
          <thead>
            <tr className="sec-head">
              <th colSpan={7} className="ce-sec">CALL (CE)</th>
              <th colSpan={1} className="mid-sec">STRIKE</th>
              <th colSpan={7} className="pe-sec">PUT (PE)</th>
            </tr>
            <tr>
              <th className="ce" style={{textAlign:'right'}}>OI</th>
              <th className="ce" style={{textAlign:'right'}}>ΔOI</th>
              <th className="ce" style={{textAlign:'right'}}>IV%</th>
              <th className="ce" style={{textAlign:'right'}}>Δ</th>
              <th className="ce" style={{textAlign:'right'}}>Bid</th>
              <th className="ce" style={{textAlign:'right'}}>Ask</th>
              <th className="ce" style={{textAlign:'right'}}>LTP</th>
              <th className="strike-h">Strike</th>
              <th className="pe" style={{textAlign:'left'}}>LTP</th>
              <th className="pe" style={{textAlign:'left'}}>Bid</th>
              <th className="pe" style={{textAlign:'left'}}>Ask</th>
              <th className="pe" style={{textAlign:'left'}}>Δ</th>
              <th className="pe" style={{textAlign:'left'}}>IV%</th>
              <th className="pe" style={{textAlign:'left'}}>ΔOI</th>
              <th className="pe" style={{textAlign:'left'}}>OI</th>
            </tr>
          </thead>
          <tbody>
            {chain.rows.map(row => (
              <tr key={row.strike} className={`${row.atm ? 'atm-row' : ''} ${row.itm_ce ? 'itm-ce' : ''}`}>
                {/* CE side */}
                <td className="r"><OIBar value={row.ce.oi} color="rgba(56,189,248,0.5)"/></td>
                <td className="r" style={{color: row.ce.oi_chg >= 0 ? 'var(--green)' : 'var(--red)', fontSize:10}}>{row.ce.oi_chg >= 0 ? '+' : ''}{row.ce.oi_chg.toLocaleString('en-IN')}</td>
                <td className="r">{row.ce.iv.toFixed(2)}</td>
                <td className="r" style={{color:'var(--cyan)'}}>{row.ce.delta.toFixed(3)}</td>
                <td className="r">{row.ce.bid.toFixed(2)}</td>
                <td className="r">{row.ce.ask.toFixed(2)}</td>
                <td className="r" style={{fontWeight:600, color: row.itm_ce ? 'var(--cyan)' : 'var(--text)'}}>{row.ce.ltp.toFixed(2)}</td>
                {/* Strike */}
                <td className="strike-cell">{row.strike}</td>
                {/* PE side */}
                <td style={{fontWeight:600, color: row.itm_pe ? 'var(--red)' : 'var(--text)'}}>{row.pe.ltp.toFixed(2)}</td>
                <td>{row.pe.bid.toFixed(2)}</td>
                <td>{row.pe.ask.toFixed(2)}</td>
                <td style={{color:'var(--red)'}}>{row.pe.delta.toFixed(3)}</td>
                <td>{row.pe.iv.toFixed(2)}</td>
                <td style={{color: row.pe.oi_chg >= 0 ? 'var(--green)' : 'var(--red)', fontSize:10}}>{row.pe.oi_chg >= 0 ? '+' : ''}{row.pe.oi_chg.toLocaleString('en-IN')}</td>
                <td><OIBar value={row.pe.oi} color="rgba(248,113,113,0.5)"/></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="chain-footer">
        <span><span style={{width:8,height:8,background:'var(--cyan-soft)',border:'1px solid var(--border-strong)',display:'inline-block'}}></span> ITM CE</span>
        <span><span style={{width:8,height:8,background:'var(--red-soft)',border:'1px solid var(--border-strong)',display:'inline-block'}}></span> ITM PE</span>
        <span>ATM strike highlighted · OI bars scaled to 9L</span>
        <span style={{marginLeft:'auto'}}>Source: GET /api/live/option-chain/{active} · 1s push</span>
      </div>
    </div>
  );
}

Object.assign(window, { PositionsPanel, EquityCurvePanel, SignalLogPanel, DepthHealthPanel, StoragePanel, AlertsPanel, SpotChartsRow, OptionChainPanel });


// ─────────────

// App shell — Live Monitor tab


function TopBar({ activeTab, onTab, brandName, brandSub }) {
  const now = useClock();
  const hh = String(now.getHours()).padStart(2,'0');
  const mm = String(now.getMinutes()).padStart(2,'0');
  const ss = String(now.getSeconds()).padStart(2,'0');
  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-mark">SD</div>
        <div>
          <div className="brand-name">{brandName}<span className="brand-sub"> · {brandSub}</span></div>
        </div>
      </div>
      <div className="tabs">
        <div className={`tab ${activeTab==='live' ? 'active' : ''}`} onClick={() => onTab('live')}>
          <Icon name="dot" size={9} color="oklch(0.78 0.18 150)"/>
          Live Monitor
        </div>
        <div className="tab disabled">Historical Explorer<span className="pill">v2</span></div>
        <div className="tab disabled">Backtests<span className="pill">v2</span></div>
      </div>
      <div className="topbar-right">
        <span className="clock">
          <span className="muted">IST</span>
          <span>{hh}<span style={{color:'var(--text-4)'}}>:</span>{mm}<span style={{color:'var(--text-4)'}}>:</span>{ss}</span>
        </span>
        <span className="badge ghost">profile · wing6_4x1</span>
        <div className="icon-btn"><Icon name="gear" size={12}/></div>
      </div>
    </div>
  );
}

function HeaderStrip({ summary }) {
  return (
    <div className="strip">
      <div className="strip-cell">
        <span className="strip-label">Session · NSE/BSE</span>
        <span className="strip-value small">2026-05-11 <span style={{color:'var(--text-3)'}}>·</span> MON</span>
        <div style={{marginTop:2}}><MarketBadge status="OPEN" /></div>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Engine</span>
        <span className="strip-value small pos">monitoring</span>
        <span className="strip-sub">pid 28471 · up 5h 12m</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Public IP · outbound</span>
        <span className="strip-value small">103.142.18.204</span>
        <span className="strip-sub">eth0 · AS134319</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">WD Free · last flush</span>
        <span className="strip-value small">1.84 <span style={{color:'var(--text-3)', fontSize:12}}>TB</span></span>
        <span className="strip-sub">flushed 0.4s ago</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Gross P&L · today</span>
        <span className={`strip-value ${summary.gross >= 0 ? 'pos' : 'neg'}`}>{fmtINR(summary.gross)}</span>
        <span className="strip-sub muted">peak {fmtINR(summary.peakGross)}</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Net P&L · today</span>
        <span className={`strip-value ${summary.net >= 0 ? 'pos' : 'neg'}`}>{fmtINR(summary.net)}</span>
        <span className="strip-sub muted">charges {fmtINR(summary.gross - summary.net, {noSign:true})}</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Quote freshness · &lt;5s</span>
        <span className="strip-value">{summary.quoteFreshness.toFixed(1)}<span style={{color:'var(--text-3)', fontSize:12}}>%</span></span>
        <Meter value={summary.quoteFreshness} max={100} warnAt={92} critAt={80}/>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Depth ready · 246 ch</span>
        <span className="strip-value">{summary.depthReady.toFixed(1)}<span style={{color:'var(--text-3)', fontSize:12}}>%</span></span>
        <Meter value={summary.depthReady} max={100} warnAt={90} critAt={75}/>
      </div>
    </div>
  );
}

function Sidebar({ audioOn, onToggleAudio, depth, wdHistory }) {
  return (
    <aside className="sidebar">
      <div className="side-section">
        <div className="side-h">Active Profile</div>
        <div className="profile-card">
          <span className="name">wing6_4x1_all_vix_filtered</span>
          <span className="sub">4 lots · 1 wing · vix &lt; 18 · bucket A/B</span>
          <span className="lock"><Icon name="lock" size={10}/> LOCKED</span>
        </div>
      </div>
      <div className="side-section">
        <div className="side-h">Connection</div>
        <div className="ws-card">
          <div className="label">
            <span className="ttl">WebSocket</span>
            <span className="sub">/ws/live · 1s push</span>
          </div>
          <div className="live-dot"/>
        </div>
        <div className="ws-card">
          <div className="label">
            <span className="ttl">Dhan Feed</span>
            <span className="sub">246 sub · 0 seq gap</span>
          </div>
          <span className="badge ok"><span className="dot"/>LIVE</span>
        </div>
      </div>
      <div className="side-section">
        <div className="side-h">Alerts</div>
        <div className="toggle-row" onClick={onToggleAudio}>
          <div className="label">
            <span className="ttl"><Icon name="speaker" size={11}/> &nbsp;Audio Alert</span>
            <span className="sub">partial_entry · forced_stale_exit</span>
          </div>
          <div className={`toggle ${audioOn ? 'on' : ''}`}/>
        </div>
      </div>
      <div className="side-section">
        <div className="side-h">API · FastAPI · :8000</div>
        <div className="api-list">
          {[
            ['GET','get','/api/live/session', '12ms'],
            ['GET','get','/api/live/positions', '24ms'],
            ['GET','get','/api/live/equity-curve', '38ms'],
            ['GET','get','/api/live/signal-log', '18ms'],
            ['GET','get','/api/live/depth-health', '14ms'],
            ['GET','get','/api/live/storage-health', '9ms'],
            ['GET','get','/api/live/alerts', '11ms'],
            ['WS','ws','/ws/live', '—'],
          ].map(([m, mc, p, lat]) => (
            <div className="api-row" key={p}>
              <span style={{display:'flex', alignItems:'center', gap:6, minWidth:0}}>
                <span className={`method ${mc}`}>{m}</span>
                <span className="path">{p}</span>
              </span>
              <span className="lat">{lat}</span>
            </div>
          ))}
        </div>
      </div>
      {/* Depth Health, Storage below API list */}
      <DepthHealthPanel depth={depth} />
      <StoragePanel wdHistory={wdHistory} />
    </aside>
  );
}

function App() {
  const [tweaks, setTweak] = useTweaks(/*EDITMODE-BEGIN*/{
    "brandName": "Strategy Monitor",
    "brandSub": "NSE · LIVE PAPER",
    "accentColor": "#4ade80",
    "showGross": false,
    "tickSpeed": 1200
  }/*EDITMODE-END*/);

  const [positions, setPositions] = useState(() => INITIAL_POSITIONS.map(p => ({...p, leg_ages: [...p.leg_ages]})));
  const [equity, setEquity] = useState(() => generateEquitySeed());
  const [depth, setDepth] = useState(() => DEPTH_HEALTH_SEED.map(d => ({...d, legs: d.legs.map(l => ({...l}))})));
  const [audioOn, setAudioOn] = useState(true);
  const [wdHistory] = useState(() => {
    const arr = []; let v = 1900;
    for (let i = 0; i < 40; i++) { v -= 0.4 + Math.random() * 0.6; arr.push(v); }
    return arr;
  });

  const [spotData] = useState(() => ({
    NIFTY:      generateSpotSeed('NIFTY'),
    FINNIFTY:   generateSpotSeed('FINNIFTY'),
    MIDCPNIFTY: generateSpotSeed('MIDCPNIFTY'),
    SENSEX:     generateSpotSeed('SENSEX'),
  }));
  const [chainData] = useState(() => ({
    NIFTY:      generateOptionChain('NIFTY'),
    FINNIFTY:   generateOptionChain('FINNIFTY'),
    MIDCPNIFTY: generateOptionChain('MIDCPNIFTY'),
    SENSEX:     generateOptionChain('SENSEX'),
  }));

  const seriesRef = useRef(null);

  useEffect(() => {
    const stateRef = { positions, equity, depth };
    const id = setInterval(() => {
      const frame = generateFrame(stateRef);
      // push equity tick into the lightweight-charts series directly
      if (seriesRef.current && frame.equity_tick) {
        seriesRef.current.update({ time: frame.equity_tick.time, value: frame.equity_tick.value });
      }
      // trigger re-render of positions + depth (mutable refs already updated)
      setPositions([...stateRef.positions]);
      setDepth([...stateRef.depth]);
      setEquity([...stateRef.equity]);
    }, 1200);
    return () => clearInterval(id);
  }, []);

  // header strip summary
  const lastEq = equity[equity.length - 1] || {};
  const peakGross = equity.reduce((m, p) => Math.max(m, p.cumulative_gross_pnl), -Infinity);
  const allAges = depth.flatMap(d => d.legs.map(l => l.age));
  const freshCount = allAges.filter(a => a < 5000).length;
  const quoteFreshness = (freshCount / allAges.length) * 100;
  const depthReadyCount = allAges.filter(a => a < 2000).length;
  const depthReady = (depthReadyCount / allAges.length) * 100;

  const summary = {
    gross: lastEq.cumulative_gross_pnl || 0,
    net: lastEq.cumulative_net_pnl || 0,
    peakGross,
    quoteFreshness,
    depthReady: 92.4 + Math.sin(Date.now()/3000) * 1.5, // smoother
  };

  return (
    <div data-screen-label="01 Live Monitor">
      <TopBar activeTab="live" onTab={() => {}} brandName={tweaks.brandName} brandSub={tweaks.brandSub} />
      <HeaderStrip summary={summary} />
      <div className="shell">
        <div className="workspace">
          <PositionsPanel positions={positions} />
          <EquityCurvePanel seriesRef={seriesRef} equityState={equity} accentColor={tweaks.accentColor} />
          <SpotChartsRow spotData={spotData} />
          {/* Bottom section: option chain → signal log → alerts, full width */}
          <OptionChainPanel chainData={chainData} />
          <SignalLogPanel entries={SIGNAL_LOG_SEED} />
          <AlertsPanel alerts={ALERTS_SEED} />
        </div>
        <Sidebar audioOn={audioOn} onToggleAudio={() => setAudioOn(v => !v)}
          depth={depth} wdHistory={wdHistory} />
      </div>
      <TweaksPanel tweaks={tweaks} setTweak={setTweak}>
        <TweakSection label="Branding">
          <TweakText label="Dashboard name" tweakKey="brandName" tweaks={tweaks} setTweak={setTweak} />
          <TweakText label="Sub-label" tweakKey="brandSub" tweaks={tweaks} setTweak={setTweak} />
        </TweakSection>
        <TweakSection label="Chart">
          <TweakColor label="Equity curve colour" tweakKey="accentColor" tweaks={tweaks} setTweak={setTweak}
            options={['#4ade80','#38bdf8','#a78bfa','#fb923c']} />
          <TweakToggle label="Show Gross P&L" tweakKey="showGross" tweaks={tweaks} setTweak={setTweak} />
        </TweakSection>
        <TweakSection label="Simulation">
          <TweakSlider label="Tick speed (ms)" tweakKey="tickSpeed" tweaks={tweaks} setTweak={setTweak} min={400} max={4000} step={200} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App/>);
