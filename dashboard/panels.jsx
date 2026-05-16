// All Live Monitor panels — live data only, no mock

const { fmtINR, fmtNum, fmtPct, ist, computePnL, depthClass, aggregateBars } = window.WingData;

// ── Empty state helper ─────────────────────────────────────────────────
function EmptyState({ icon, title, sub }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', padding: '28px 0', gap: 8, opacity: 0.55,
    }}>
      <span style={{ fontSize: 22 }}>{icon}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', letterSpacing: '0.06em' }}>{title}</span>
      {sub && <span style={{ fontSize: 10, color: 'var(--text-4)' }}>{sub}</span>}
    </div>
  );
}

// ── Positions panel ────────────────────────────────────────────────────
const PositionsPanel = React.memo(function PositionsPanel({ positions, closedTrades }) {
  const [activeTab, setActiveTab] = useState('open');

  return (
    <div className="panel span-positions">
      <div className="panel-header">
        <div className="panel-title">
          {activeTab === 'open' ? 'Open Positions' : 'Closed Positions'}
          <span className="count">
            {activeTab === 'open'
              ? (positions.length > 0 ? `${positions.length} · 4×1 IRON CONDOR` : 'NONE TODAY')
              : (closedTrades.length > 0 ? `${closedTrades.length} TRADES TODAY` : 'NONE CLOSED YET')
            }
          </span>
        </div>
        <div className="panel-actions">
          <span className="badge ghost"><Icon name="dot" size={10} color="oklch(0.78 0.18 150)"/> SYNCED · 1s</span>
          <div className="icon-btn"><Icon name="expand" size={11}/></div>
        </div>
      </div>
      <div style={{display:'flex', borderBottom:'1px solid var(--border)', padding:'0 14px', gap:0}}>
        {['open','closed'].map(tab => (
          <div
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding:'6px 14px', fontSize:10, fontWeight:600, letterSpacing:'0.07em',
              cursor:'pointer', color: activeTab === tab ? 'var(--text)' : 'var(--text-3)',
              borderBottom: activeTab === tab ? '2px solid var(--green)' : '2px solid transparent',
              textTransform:'uppercase', userSelect:'none',
            }}
          >
            {tab === 'open' ? `Open (${positions.length})` : `Closed (${closedTrades.length})`}
          </div>
        ))}
      </div>
      <div className="panel-body" style={{overflowX: 'auto'}}>
        {activeTab === 'open' ? (
          positions.length === 0 ? (
            <EmptyState icon="○" title="NO OPEN POSITIONS" sub="No trades entered this session · entries happen at 09:20 IST" />
          ) : (
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
                            a != null
                              ? <span key={i} className={`age-chip ${a > 4000 ? 'crit' : a > 2000 ? 'warn' : ''}`}>{Math.round(a)}</span>
                              : <span key={i} className="age-chip" style={{opacity:0.4}}>—</span>
                          ))}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        ) : (
          closedTrades.length === 0 ? (
            <EmptyState icon="◎" title="NO CLOSED POSITIONS" sub="Completed trades appear here after exit at 15:20 IST" />
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th className="l">Symbol</th>
                  <th className="l">Short CE / Long CE</th>
                  <th className="l">Short PE / Long PE</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Exit Reason</th>
                  <th>Credit ₹</th>
                  <th>Debit ₹</th>
                  <th>Gross P&L</th>
                  <th>Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {closedTrades.map((t, i) => (
                  <tr key={`${t.symbol}-${i}`} className={`pos-row ${t.net_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
                    <td className="l">
                      <div className="sym-cell">
                        <span className="sym">{t.symbol}</span>
                        <span className="exp">{t.expiry} · {t.lots}×{t.lot_size}</span>
                      </div>
                    </td>
                    <td className="l">
                      <span className="legpair">
                        <span className="short">{t.short_call_strike}CE</span>
                        <span className="sep">/</span>
                        <span className="long">{t.long_call_strike}CE</span>
                      </span>
                    </td>
                    <td className="l">
                      <span className="legpair">
                        <span className="short">{t.short_put_strike}PE</span>
                        <span className="sep">/</span>
                        <span className="long">{t.long_put_strike}PE</span>
                      </span>
                    </td>
                    <td className="muted">{(t.entry_time || '').slice(11, 19) || '—'}</td>
                    <td className="muted">{(t.exit_time || '').slice(11, 19) || '—'}</td>
                    <td>
                      <span style={{fontSize:9, letterSpacing:'0.05em', color: t.forced_stale_exit ? 'var(--amber)' : 'var(--text-3)'}}>
                        {t.exit_reason}{t.forced_stale_exit ? ' ⚠' : ''}
                      </span>
                    </td>
                    <td>{fmtNum(t.entry_credit)}</td>
                    <td>{fmtNum(t.exit_debit)}</td>
                    <td className={t.gross_pnl >= 0 ? 'pos' : 'neg'}>{fmtINR(t.gross_pnl)}</td>
                    <td className={t.net_pnl >= 0 ? 'pos' : 'neg'} style={{fontWeight:600}}>{fmtINR(t.net_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
});

// ── Equity curve ───────────────────────────────────────────────────────
const EquityCurvePanel = React.memo(function EquityCurvePanel({ seriesRef, equityState, accentColor = '#4ade80' }) {
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
      lineColor: accentColor,
      topColor: 'rgba(74,222,128,0.28)',
      bottomColor: 'rgba(74,222,128,0.0)',
      lineWidth: 1.6,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });
    chartRef.current = chart;
    netSeriesRef.current = series;
    seriesRef.current = series;
    return () => { chart.remove(); };
  }, []);

  // Load/update equity data when it arrives
  useEffect(() => {
    if (!netSeriesRef.current || !equityState.length) return;
    netSeriesRef.current.setData(equityState.map(p => ({ time: p.time, value: p.cumulative_net_pnl })));
    setTimeout(() => chartRef.current?.timeScale().fitContent(), 80);
  }, [equityState]);

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
  const high = equityState.length ? Math.max(...equityState.map(p => p.cumulative_net_pnl ?? 0)) : 0;

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
      <div style={{position:'relative'}}>
        <div ref={containerRef} className="eq-chart" />
        {equityState.length === 0 && (
          <div style={{position:'absolute', inset:0, display:'flex', alignItems:'center', justifyContent:'center', background:'#111'}}>
            <EmptyState icon="◈" title="NO TRADES YET" sub="Equity curve populates when positions are closed" />
          </div>
        )}
      </div>
    </div>
  );
});

// ── Signal log ─────────────────────────────────────────────────────────
const SignalLogPanel = React.memo(function SignalLogPanel({ entries }) {
  return (
    <div className="panel span-alerts">
      <div className="panel-header">
        <div className="panel-title">Signal Log <span className="count">{entries.length} EVENTS · TODAY</span></div>
        <div className="panel-actions">
          <span className="badge ghost">SKIP {entries.filter(e=>e.event==='skip').length}</span>
          <span className="badge ok">ENTRY {entries.filter(e=>e.event==='entry').length}</span>
          <div className="icon-btn"><Icon name="filter" size={11}/></div>
        </div>
      </div>
      <div className="panel-body sig-list" style={{maxHeight: 280, overflowY:'auto'}}>
        {entries.length === 0
          ? <EmptyState icon="⊘" title="NO SIGNALS YET" sub="Entry/skip events appear here at 09:20 IST" />
          : entries.map((e, i) => (
            <div className="sig-row" key={`${e.ts}-${e.symbol}-${i}`}>
              <span className="ts">{e.ts}</span>
              <span className={`ev ${e.event}`}>{e.event === 'entry' ? 'E' : e.event === 'exit' ? 'X' : 'S'}</span>
              <span className="sym">{e.symbol}</span>
              <span className="reason">{e.reason}</span>
              <span className="meta">
                {e.vix != null ? `vix ${e.vix.toFixed(2)}` : ''}
                {e.dte != null ? ` · dte ${e.dte}` : ''}
                {e.bucket ? ` · bkt ${e.bucket}` : ''}
              </span>
            </div>
          ))
        }
      </div>
    </div>
  );
});

// ── Depth Health (aggregate summary) ──────────────────────────────────
const DepthHealthPanel = React.memo(function DepthHealthPanel({ depthSummary }) {
  const ds = depthSummary;
  const ready    = ds?.ready ?? 0;
  const total    = ds?.total ?? 0;
  const readyPct = ds?.ready_pct ?? 0;
  const ageS     = ds?.snapshot_age_s;
  const stale    = ageS != null && ageS > 15;

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">
          Depth Cache
          <span className="count">{total > 0 ? `${total} INSTRUMENTS` : 'OFFLINE'}</span>
        </div>
        <div className="panel-actions">
          <span className={`badge ${stale ? 'ghost' : (readyPct >= 90 ? 'ok' : 'ghost')}`}>
            {stale ? 'STALE' : `${readyPct.toFixed(0)}% RDY`}
          </span>
        </div>
      </div>
      <div className="panel-body" style={{padding: '10px 14px 14px'}}>
        {total === 0 ? (
          <EmptyState icon="⊗" title="COLLECTOR OFFLINE" sub="Depth data unavailable" />
        ) : (
          <>
            <div style={{display:'flex', justifyContent:'space-between', fontSize:10, color:'var(--text-3)', marginBottom:6}}>
              <span>Ready: <strong style={{color: readyPct >= 90 ? 'var(--green)' : 'var(--amber)'}}>{ready}/{total}</strong></span>
              {ageS != null && <span style={{color: stale ? 'var(--red)' : 'var(--text-3)'}}>snapshot {ageS.toFixed(0)}s ago</span>}
            </div>
            <div style={{background:'var(--surface-2)', borderRadius:3, height:8, overflow:'hidden'}}>
              <div style={{
                height:'100%', width:`${Math.min(100, readyPct)}%`,
                background: readyPct >= 90 ? 'var(--green)' : readyPct >= 75 ? 'var(--amber)' : 'var(--red)',
                transition: 'width 0.4s ease',
              }}/>
            </div>
            <div style={{marginTop:6, display:'flex', gap:14, fontSize:10, color:'var(--text-4)'}}>
              <span>Configured: {ds?.configured ?? '—'}</span>
              <span>Tracked: {ds?.tracked ?? '—'}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
});

// ── Storage / writer health ────────────────────────────────────────────
const StoragePanel = React.memo(function StoragePanel({ wdHistory, storage }) {
  const live = storage != null;
  const rawAge     = live && storage.raw_packet_flush_age_s != null
    ? `${storage.raw_packet_flush_age_s.toFixed(2)}s` : '—';
  const parquetAge = live && storage.parquet_flush_age_s != null
    ? `${storage.parquet_flush_age_s.toFixed(2)}s` : '—';
  const wdFree     = live && storage.wd_free_gb != null
    ? `${(storage.wd_free_gb / 1000).toFixed(2)} TB` : '—';
  const mountOk    = live ? (storage.wd_mount_ok !== false) : null;
  const rawGood    = live ? (storage.raw_packet_flush_age_s != null && storage.raw_packet_flush_age_s < 2) : false;

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">
          Storage · Writer Health
          <span className="count">{mountOk === null ? '—' : (mountOk ? 'WD MOUNT OK' : 'WD MOUNT ERR')}</span>
        </div>
        <div className="panel-actions">
          <span className={`badge ${mountOk ? 'ok' : 'ghost'}`}><span className="dot"/>FLUSHING</span>
        </div>
      </div>
      <div className="panel-body">
        <div className="stor-grid">
          <div className="stor-cell">
            <span className="lbl">Raw Packet Flush Age</span>
            <span className={`val ${rawGood ? 'pos' : (live ? 'neg' : '')}`}>{rawAge}</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">Parquet Flush Age</span>
            <span className="val">{parquetAge}</span>
          </div>
          <div className="stor-cell">
            <span className="lbl">WD Free Space</span>
            <span className="val">{wdFree}</span>
            {wdHistory.length > 0 && (
              <div style={{marginTop:2}}>
                <Sparkline data={wdHistory} width={200} height={22} color="oklch(0.78 0.12 220)"/>
              </div>
            )}
          </div>
          <div className="stor-cell">
            <span className="lbl">Depth Cache Age</span>
            <span className="val">{live && storage.depth_cache_age_s != null ? `${storage.depth_cache_age_s.toFixed(1)}s` : '—'}</span>
          </div>
        </div>
      </div>
    </div>
  );
});

// ── Alerts timeline ────────────────────────────────────────────────────
const AlertsPanel = React.memo(function AlertsPanel({ alerts }) {
  const counts = alerts.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc; }, {});
  return (
    <div className="panel span-alerts">
      <div className="panel-header">
        <div className="panel-title">Alert Timeline <span className="count">{alerts.length} TODAY · TELEGRAM</span></div>
        <div className="panel-actions">
          {counts.critical > 0 && <span className="badge neg">{counts.critical} CRIT</span>}
          {counts.warning  > 0 && <span className="badge ghost">{counts.warning} WARN</span>}
        </div>
      </div>
      <div className="panel-body">
        <div className="alert-summary">
          <div><div className="lbl">Critical</div><div className="val neg">{counts.critical || 0}</div></div>
          <div><div className="lbl">Warning</div><div className="val" style={{color:'var(--amber)'}}>{counts.warning || 0}</div></div>
          <div><div className="lbl">Info</div><div className="val" style={{color:'var(--cyan)'}}>{counts.info || 0}</div></div>
        </div>
        {alerts.length === 0
          ? <EmptyState icon="✓" title="NO ALERTS TODAY" sub="Health monitor checks every 30s" />
          : (
            <div className="timeline">
              {alerts.map((a, i) => (
                <div className="alert-row" key={`${a.ts}-${a.severity}-${a.component}-${i}`}>
                  <span className="ts">{a.ts}</span>
                  <span className={`badge ${a.severity}`}>{a.severity.toUpperCase()}</span>
                  <span className="comp">{a.component}</span>
                  <span className="msg">{a.message}</span>
                </div>
              ))}
            </div>
          )
        }
      </div>
    </div>
  );
});

// ── Spot charts ────────────────────────────────────────────────────────
const TF_OPTIONS = [
  { label: '1m',  minutes: 1 },
  { label: '5m',  minutes: 5 },
  { label: '15m', minutes: 15 },
  { label: '1H',  minutes: 60 },
  { label: '1D',  minutes: 99999 },
];

function SpotChartCard({ symbol, bars, marketClosed }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [tf, setTf] = useState(1);

  // Create chart on mount unconditionally — data is set by the bars effect below
  useEffect(() => {
    if (!containerRef.current || !window.LightweightCharts) return;
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
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  // Set/update data whenever bars or tf changes
  useEffect(() => {
    if (!seriesRef.current) return;
    if (!bars.length) return;
    const tfOpt = TF_OPTIONS.find(t => t.minutes === tf) || TF_OPTIONS[0];
    seriesRef.current.setData(aggregateBars(bars, tfOpt.minutes));
    setTimeout(() => chartRef.current?.timeScale().fitContent(), 50);
  }, [bars, tf]);

  const last  = bars[bars.length - 1] || {};
  const first = bars[0] || {};
  const chg    = (last.close || 0) - (first.open || 0);
  const chgPct = first.open ? (chg / first.open) * 100 : 0;

  return (
    <div className="panel">
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
          {bars.length ? (
            <>
              <span className={`spot-price ${chg >= 0 ? 'pos' : 'neg'}`}>{last.close?.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
              <span className={`spot-change ${chg >= 0 ? 'pos' : 'neg'}`}>{chg >= 0 ? '+' : ''}{chg.toFixed(2)} ({chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%)</span>
              {marketClosed && <span className="badge ghost" style={{marginLeft:6, fontSize:9}}>LAST SESSION</span>}
            </>
          ) : (
            <span className="spot-price" style={{opacity:0.4}}>loading…</span>
          )}
        </div>
        <span className="spot-meta">{bars.length ? `H ${last.high?.toFixed(2)} · L ${last.low?.toFixed(2)}` : ''}</span>
      </div>
      {/* Container always rendered so ref is available on mount */}
      <div style={{position:'relative'}}>
        <div ref={containerRef} className="spot-chart-container" />
        {!bars.length && (
          <div style={{
            position:'absolute', inset:0, display:'flex', alignItems:'center',
            justifyContent:'center', background:'#111', borderRadius:2,
          }}>
            <EmptyState icon="↗" title="LOADING" sub={`${symbol} spot bars`} />
          </div>
        )}
      </div>
    </div>
  );
}

const SpotChartsRow = React.memo(function SpotChartsRow({ spotData, marketClosed }) {
  return (
    <div style={{display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:'12px', gridColumn:'span 12'}}>
      {['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'].map(sym => (
        <SpotChartCard key={sym} symbol={sym} bars={spotData[sym] || []} marketClosed={marketClosed} />
      ))}
    </div>
  );
});

// ── Option chain panel ─────────────────────────────────────────────────
const OI_MAX = 900000;
function OIBar({ value, color }) {
  const pct = Math.min(100, ((value || 0) / OI_MAX) * 100);
  return (
    <div className="oi-bar">
      <span>{(value || 0).toLocaleString('en-IN')}</span>
      <div className="bar"><div className="bar-fill" style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

function _chainSpot(rows) {
  // Estimate spot from the row where CE and PE LTP are closest (ATM proxy)
  if (!rows.length) return null;
  let bestRow = rows[0], bestDiff = Infinity;
  for (const r of rows) {
    const ceLtp = r.ce?.ltp || 0;
    const peLtp = r.pe?.ltp || 0;
    if (ceLtp > 0 && peLtp > 0) {
      const diff = Math.abs(ceLtp - peLtp);
      if (diff < bestDiff) { bestDiff = diff; bestRow = r; }
    }
  }
  // Put-call parity: spot ≈ strike + CE_ltp - PE_ltp
  const ce = bestRow.ce?.ltp || 0, pe = bestRow.pe?.ltp || 0;
  return bestRow.strike + ce - pe;
}

const OptionChainPanel = React.memo(function OptionChainPanel({ chainData, chainLoaded, marketClosed, session }) {
  const symbols = ['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'];
  const [active, setActive] = useState('NIFTY');
  const rows = chainData[active] || [];

  const engineOffline = !session || session.engine_phase === 'offline' || !session.feed_connected;
  const noData = chainLoaded && rows.length === 0;

  // ATM filtering is done in the bridge (all 4 symbols); rows are already ATM ± 15
  const spot = rows.length ? _chainSpot(rows) : null;
  const step = rows.length > 1 ? rows[1].strike - rows[0].strike : 100;
  const atmStrike = spot != null ? Math.round(spot / step) * step : null;
  const visibleRows = rows;  // bridge pre-filters; keep for isAtm highlight only

  return (
    <div className="panel span-alerts" style={{position:'relative'}}>
      <div className="panel-header">
        <div className="panel-title">
          Option Chain
          <span className="count">DHAN LIVE · ATM ± 15 STRIKES</span>
        </div>
        <div className="panel-actions">
          {marketClosed
            ? <span className="badge ghost">MARKET CLOSED</span>
            : (engineOffline
              ? <span className="badge ghost">ENGINE OFFLINE</span>
              : <span className="badge ok"><span className="dot"/>QUOTE LIVE</span>)
          }
          <div className="icon-btn"><Icon name="export" size={11}/></div>
        </div>
      </div>
      <div className="chain-tabs">
        {symbols.map(s => {
          const sRows = chainData[s] || [];
          const sSpot = sRows.length ? _chainSpot(sRows) : null;
          return (
            <div key={s} className={`chain-tab ${active === s ? 'active' : ''}`} onClick={() => setActive(s)}>
              {s}
              {sSpot != null && (
                <span className="spot-chip">{sSpot.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>
              )}
            </div>
          );
        })}
        <div style={{flex:1, borderBottom:'none'}} />
        {spot != null && (
          <div style={{padding:'9px 14px', fontSize:10, color:'var(--text-3)', fontFamily:'JetBrains Mono, monospace', display:'flex', alignItems:'center', gap:10}}>
            <span>ATM <strong style={{color:'var(--text)'}}>{atmStrike}</strong></span>
            <span>·</span>
            <span>Spot <strong style={{color:'var(--text)'}}>{spot?.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}</strong></span>
          </div>
        )}
      </div>

      {/* Blocking overlays only for engine-offline / no-data — NOT for market closed */}
      {!marketClosed && (engineOffline || noData) && (
        <div style={{
          background:'rgba(10,10,10,0.82)', position:'absolute', inset:0,
          zIndex:10, display:'flex', flexDirection:'column',
          alignItems:'center', justifyContent:'center', gap:8,
          backdropFilter:'blur(2px)', borderRadius:4,
          top: 96,
        }}>
          {engineOffline
            ? <>
                <span style={{fontSize:13, fontWeight:600, color:'var(--text-2)', letterSpacing:'0.08em'}}>ENGINE OFFLINE</span>
                <span style={{fontSize:10, color:'var(--text-4)'}}>Option chain data written by paper engine · starts 09:00 IST</span>
              </>
            : <>
                <span style={{fontSize:13, fontWeight:600, color:'var(--text-2)', letterSpacing:'0.08em'}}>NO CHAIN DATA</span>
                <span style={{fontSize:10, color:'var(--text-4)'}}>Engine has not fetched chains yet · available after 09:15 IST</span>
              </>
          }
        </div>
      )}
      {/* When market is closed show EOD snapshot banner inline (no blocking overlay) */}
      {marketClosed && rows.length > 0 && (
        <div style={{
          margin:'0 14px 8px', padding:'6px 12px', borderRadius:3,
          background:'rgba(255,255,255,0.04)', border:'1px solid rgba(255,255,255,0.07)',
          display:'flex', alignItems:'center', gap:8, fontSize:10, color:'var(--text-3)',
        }}>
          <span style={{fontWeight:600, letterSpacing:'0.06em', color:'var(--text-2)'}}>EOD SNAPSHOT</span>
          <span>·</span>
          <span>End-of-day quotes · live feed resumes 09:15 IST next trading day</span>
        </div>
      )}

      {rows.length > 0 && (
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
                <th className="ce" style={{textAlign:'right'}}>IV%</th>
                <th className="ce" style={{textAlign:'right'}}>Δ</th>
                <th className="ce" style={{textAlign:'right'}}>Bid</th>
                <th className="ce" style={{textAlign:'right'}}>Ask</th>
                <th className="ce" style={{textAlign:'right'}}>LTP</th>
                <th className="ce" style={{textAlign:'right'}}>Age</th>
                <th className="strike-h">Strike</th>
                <th className="pe" style={{textAlign:'left'}}>Age</th>
                <th className="pe" style={{textAlign:'left'}}>LTP</th>
                <th className="pe" style={{textAlign:'left'}}>Bid</th>
                <th className="pe" style={{textAlign:'left'}}>Ask</th>
                <th className="pe" style={{textAlign:'left'}}>Δ</th>
                <th className="pe" style={{textAlign:'left'}}>IV%</th>
                <th className="pe" style={{textAlign:'left'}}>OI</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(row => {
                const ce = row.ce || {};
                const pe = row.pe || {};
                const isAtm = row.strike === atmStrike;
                const itmCe = spot != null && row.strike < spot;
                const ceAgeClass = depthClass(ce.age_ms || 0);
                const peAgeClass = depthClass(pe.age_ms || 0);
                return (
                  <tr key={row.strike} className={`${isAtm ? 'atm-row' : ''} ${itmCe ? 'itm-ce' : ''}`}>
                    <td className="r"><OIBar value={ce.oi || 0} color="rgba(56,189,248,0.5)"/></td>
                    <td className="r">{ce.iv != null ? ce.iv.toFixed(2) : '—'}</td>
                    <td className="r" style={{color:'var(--cyan)'}}>{ce.delta != null ? ce.delta.toFixed(3) : '—'}</td>
                    <td className="r">{ce.bid > 0 ? ce.bid.toFixed(2) : '—'}</td>
                    <td className="r">{ce.ask > 0 ? ce.ask.toFixed(2) : '—'}</td>
                    <td className="r" style={{fontWeight:600, color: itmCe ? 'var(--cyan)' : 'var(--text)'}}>
                      {ce.ltp > 0 ? ce.ltp.toFixed(2) : '—'}
                    </td>
                    <td className={`r ${ceAgeClass}`} style={{fontSize:9}}>
                      {ce.age_ms > 0 ? `${(ce.age_ms/1000).toFixed(1)}s` : '—'}
                    </td>
                    <td className="strike-cell">{row.strike}</td>
                    <td className={`${peAgeClass}`} style={{fontSize:9}}>
                      {pe.age_ms > 0 ? `${(pe.age_ms/1000).toFixed(1)}s` : '—'}
                    </td>
                    <td style={{fontWeight:600, color: (spot != null && row.strike > spot) ? 'var(--red)' : 'var(--text)'}}>
                      {pe.ltp > 0 ? pe.ltp.toFixed(2) : '—'}
                    </td>
                    <td>{pe.bid > 0 ? pe.bid.toFixed(2) : '—'}</td>
                    <td>{pe.ask > 0 ? pe.ask.toFixed(2) : '—'}</td>
                    <td style={{color:'var(--red)'}}>{pe.delta != null ? pe.delta.toFixed(3) : '—'}</td>
                    <td>{pe.iv != null ? pe.iv.toFixed(2) : '—'}</td>
                    <td><OIBar value={pe.oi || 0} color="rgba(248,113,113,0.5)"/></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="chain-footer">
        <span><span style={{width:8,height:8,background:'var(--cyan-soft)',border:'1px solid var(--border-strong)',display:'inline-block'}}></span> ITM CE</span>
        <span>ATM strike highlighted · depth age colour-coded</span>
        <span style={{marginLeft:'auto'}}>Source: engine snapshots · 5s refresh</span>
      </div>
    </div>
  );
});

Object.assign(window, { PositionsPanel, EquityCurvePanel, SignalLogPanel, DepthHealthPanel, StoragePanel, AlertsPanel, SpotChartsRow, OptionChainPanel });
