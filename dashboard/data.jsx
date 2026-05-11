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
