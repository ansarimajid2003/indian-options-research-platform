// All Live Monitor panels

const { fmtINR, fmtNum, fmtPct, ist, computePnL, depthClass } = window.WingData;

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
    // Apply any pending data from the initial REST fetch that happened before mount
    if (window.__pendingEquityData) {
      const pending = window.__pendingEquityData.map(p => ({ time: p.time, value: p.cumulative_net_pnl }));
      series.setData(pending);
      window.__pendingEquityData = null;
    }
    setTimeout(() => chart.timeScale().fitContent(), 80);
    return () => { chart.remove(); };
  }, []);

  useEffect(() => {
    if (!netSeriesRef.current) return;
    const hex = accentColor.replace('#', '');
    const r = parseInt(hex.slice(0,2),16), g = parseInt(hex.slice(2,4),16), b = parseInt(hex.slice(4,6),16);
    netSeriesRef.current.applyOptions({
      lineColor: accentColor,
      topColor: `rgba(${r},${g},${b},0.28)`,
      bottomColor: `rgba(${r},${g},${b},0.0)`,
    });
  }, [accentColor]);

  const last = equityState[equityState.length - 1] || {};
  const high = Math.max(...equityState.map(p => p.cumulative_net_pnl ?? 0));
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
          <div className={`val ${(last.cumulative_net_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmtINR(last.cumulative_net_pnl)}</div>
          <div className="delta muted">peak {fmtINR(high)}</div>
        </div>
        <div>
          <div className="lbl">Gross P&L</div>
          <div className={`val ${(last.cumulative_gross_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmtINR(last.cumulative_gross_pnl)}</div>
          <div className="delta muted">charges {fmtINR((last.cumulative_gross_pnl ?? 0) - (last.cumulative_net_pnl ?? 0), {noSign:true})}</div>
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
function StoragePanel({ wdHistory, storage }) {
  const live = storage != null;
  const rawAge     = live && storage.raw_packet_flush_age_s != null
    ? `${storage.raw_packet_flush_age_s.toFixed(2)}s` : (live ? '—' : '0.42s');
  const parquetAge = live && storage.parquet_flush_age_s != null
    ? `${storage.parquet_flush_age_s.toFixed(2)}s` : (live ? '—' : '3.81s');
  const wdFree     = live && storage.wd_free_gb != null
    ? `${(storage.wd_free_gb / 1000).toFixed(2)} TB` : (live ? '—' : '1.84 TB');
  const mountOk    = live ? (storage.wd_mount_ok !== false) : true;
  const rawGood    = live ? (storage.raw_packet_flush_age_s != null && storage.raw_packet_flush_age_s < 2) : true;

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">
          Storage · Writer Health
          <span className="count">{mountOk ? 'WD MOUNT OK' : 'WD MOUNT ERR'}</span>
        </div>
        <div className="panel-actions">
          <span className={`badge ${mountOk ? 'ok' : 'ghost'}`}><span className="dot"/>FLUSHING</span>
        </div>
      </div>
      <div className="panel-body">
        <div className="stor-grid">
          <div className="stor-cell">
            <span className="lbl">Raw Packet Flush Age</span>
            <span className={`val ${rawGood ? 'pos' : 'neg'}`}>{rawAge}</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">Parquet Flush Age</span>
            <span className="val">{parquetAge}</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">WD Free Space</span>
            <span className="val">{wdFree}</span>
            <div style={{marginTop:2}}>
              <Sparkline data={wdHistory} width={200} height={22} color="oklch(0.78 0.12 220)"/>
            </div>
          </div>
          <div className="stor-cell">
            <span className="lbl">Backpressure</span>
            <span className="val pos">CLEAR</span>
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
          <div><div className="lbl">Last Telegram</div><div className="val" style={{fontSize:14}}>{lastSent?.delivery_ts || '—'}</div><div className="muted" style={{fontSize:10}}>delta ~2s</div></div>
          <div><div className="lbl">External Heartbeat</div><div className="val pos" style={{fontSize:14}}>healthchecks.io</div></div>
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
function SpotChartCard({ symbol, bars, marketClosed }) {
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

  // Reload chart data when bars are replaced (e.g. API data arriving after mount)
  useEffect(() => {
    if (!seriesRef.current || !bars.length) return;
    const tfOpt = TF_OPTIONS.find(t => t.minutes === tf) || TF_OPTIONS[0];
    seriesRef.current.setData(aggregateBars(bars, tfOpt.minutes));
    setTimeout(() => chartRef.current?.timeScale().fitContent(), 50);
  }, [bars]);

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
          {marketClosed && <span className="badge ghost" style={{marginLeft:6, fontSize:9}}>LAST SESSION</span>}
        </div>
        <span className="spot-meta">H {last.high?.toFixed(2)} · L {last.low?.toFixed(2)}</span>
      </div>
      <div ref={containerRef} className="spot-chart-container" />
    </div>
  );
}

function SpotChartsRow({ spotData, marketClosed }) {
  return (
    <>
      {['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'].map(sym => (
        <SpotChartCard key={sym} symbol={sym} bars={spotData[sym] || []} marketClosed={marketClosed} />
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

function OptionChainPanel({ chainData, marketClosed }) {
  const symbols = ['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'];
  const [active, setActive] = useState('NIFTY');
  const chain = chainData[active] || { rows: [], spot: 0 };

  return (
    <div className="panel span-alerts" style={{position:'relative'}}>
      <div className="panel-header">
        <div className="panel-title">
          Option Chain
          <span className="count">NSE · LIVE · ATM ± 10 STRIKES</span>
        </div>
        <div className="panel-actions">
          <span className="badge ghost">EXP NEAREST</span>
          {marketClosed
            ? <span className="badge ghost">MARKET CLOSED</span>
            : <span className="badge ok"><span className="dot"/>QUOTE LIVE</span>}
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
      {marketClosed && (
        <div style={{
          background:'rgba(10,10,10,0.82)', position:'absolute', inset:0,
          zIndex:10, display:'flex', flexDirection:'column',
          alignItems:'center', justifyContent:'center', gap:8,
          backdropFilter:'blur(2px)', borderRadius:4,
        }}>
          <span style={{fontSize:13, fontWeight:600, color:'var(--text-2)', letterSpacing:'0.08em'}}>MARKET CLOSED</span>
          <span style={{fontSize:10, color:'var(--text-4)'}}>Live quotes unavailable · opens 09:15 IST on next trading day</span>
        </div>
      )}
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
                <td className="r"><OIBar value={row.ce.oi} color="rgba(56,189,248,0.5)"/></td>
                <td className="r" style={{color: row.ce.oi_chg >= 0 ? 'var(--green)' : 'var(--red)', fontSize:10}}>{row.ce.oi_chg >= 0 ? '+' : ''}{row.ce.oi_chg.toLocaleString('en-IN')}</td>
                <td className="r">{row.ce.iv.toFixed(2)}</td>
                <td className="r" style={{color:'var(--cyan)'}}>{row.ce.delta.toFixed(3)}</td>
                <td className="r">{row.ce.bid.toFixed(2)}</td>
                <td className="r">{row.ce.ask.toFixed(2)}</td>
                <td className="r" style={{fontWeight:600, color: row.itm_ce ? 'var(--cyan)' : 'var(--text)'}}>{row.ce.ltp.toFixed(2)}</td>
                <td className="strike-cell">{row.strike}</td>
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
        <span style={{marginLeft:'auto'}}>Source: Dhan live feed · 1s push</span>
      </div>
    </div>
  );
}

Object.assign(window, { PositionsPanel, EquityCurvePanel, SignalLogPanel, DepthHealthPanel, StoragePanel, AlertsPanel, SpotChartsRow, OptionChainPanel });
