// App shell — wired to /ws/live and /api/live/* endpoints

const { fmtINR, fmtNum, fmtPct, ist, computePnL } = window.WingData;

// ── Adapters: API model → UI shape ──────────────────────────────────────

function adaptPosition(apiPos) {
  const legs = apiPos.legs || [];
  const findLeg = role => legs.find(l => l.leg_role === role) || {};
  const sCe = findLeg('short_call');
  const lCe = findLeg('long_call');
  const sPe = findLeg('short_put');
  const lPe = findLeg('long_put');
  return {
    symbol:           apiPos.symbol,
    expiry:           apiPos.expiry,
    lots:             apiPos.lots,
    lot_size:         apiPos.lot_size,
    entry_time:       (apiPos.entry_time || '').slice(11, 19) || '—',
    entry_credit:     apiPos.entry_credit,
    entry_charges:    apiPos.entry_charges,
    short_ce_strike:  sCe.strike  || 0,
    long_ce_strike:   lCe.strike  || 0,
    short_pe_strike:  sPe.strike  || 0,
    long_pe_strike:   lPe.strike  || 0,
    short_ce_premium: sCe.price   || 0,
    long_ce_premium:  lCe.price   || 0,
    short_pe_premium: sPe.price   || 0,
    long_pe_premium:  lPe.price   || 0,
    current_mark:  apiPos.current_mark ?? apiPos.entry_credit,
    leg_ages: [
      sCe.quote_age_ms ?? null,
      lCe.quote_age_ms ?? null,
      sPe.quote_age_ms ?? null,
      lPe.quote_age_ms ?? null,
    ],
  };
}

function adaptEquityPoint(apiEq) {
  const ms = new Date(apiEq.ts).getTime();
  return {
    time: Math.floor(ms / 1000),
    cumulative_gross_pnl: apiEq.cumulative_gross_pnl,
    cumulative_net_pnl:   apiEq.cumulative_net_pnl,
  };
}

function _timeOnly(ts) {
  if (!ts) return '—';
  const m = ts.match(/T?(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts;
}

function adaptAlert(apiAlert) {
  const timeStr = _timeOnly(apiAlert.ts);
  return {
    ts:        timeStr,
    severity:  apiAlert.severity,
    component: apiAlert.component,
    reason:    apiAlert.reason,
    message:   apiAlert.message,
  };
}

function adaptSignalLog(apiEntry) {
  return {
    ts:     _timeOnly(apiEntry.ts),
    event:  apiEntry.event,
    symbol: apiEntry.symbol,
    reason: apiEntry.reason,
    vix:    apiEntry.vix,
    dte:    apiEntry.dte,
    bucket: apiEntry.bucket,
  };
}

// ── TopBar ───────────────────────────────────────────────────────────────
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

// ── HeaderStrip ──────────────────────────────────────────────────────────
function HeaderStrip({ summary, session }) {
  const sess = session || {};
  const sessionDate  = sess.session_date || '—';
  const dayOfWeek    = sessionDate !== '—'
    ? new Date(sessionDate + 'T00:00:00').toLocaleDateString('en-US', {weekday:'short'}).toUpperCase()
    : '—';
  const marketStatus = sess.market_status || 'CLOSED';
  const enginePhase  = sess.engine_phase  || 'offline';
  const enginePid    = sess.engine_pid    != null ? sess.engine_pid : '—';
  const subCount     = sess.feed_subscribed_count != null ? sess.feed_subscribed_count : 0;

  return (
    <div className="strip">
      <div className="strip-cell">
        <span className="strip-label">Session · NSE/BSE</span>
        <span className="strip-value small">{sessionDate} <span style={{color:'var(--text-3)'}}>·</span> {dayOfWeek}</span>
        <div style={{marginTop:2}}><MarketBadge status={marketStatus} /></div>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Engine</span>
        <span className="strip-value small pos">{enginePhase}</span>
        <span className="strip-sub">pid {enginePid}</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Feed · positions</span>
        <span className={`strip-value small ${sess.feed_connected ? 'pos' : 'neg'}`}>
          {sess.feed_connected ? 'LIVE' : 'DISC'}
        </span>
        <span className="strip-sub">{sess.open_position_count ?? 0} pos · {subCount} sub</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Gross P&L · today</span>
        <span className={`strip-value ${(summary.gross||0) >= 0 ? 'pos' : 'neg'}`}>{fmtINR(summary.gross)}</span>
        <span className="strip-sub muted">peak {fmtINR(summary.peakGross)}</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Net P&L · today</span>
        <span className={`strip-value ${(summary.net||0) >= 0 ? 'pos' : 'neg'}`}>{fmtINR(summary.net)}</span>
        <span className="strip-sub muted">charges {fmtINR((summary.gross||0) - (summary.net||0), {noSign:true})}</span>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Quote freshness · &lt;5s</span>
        <span className="strip-value">{(summary.quoteFreshness||0).toFixed(1)}<span style={{color:'var(--text-3)', fontSize:12}}>%</span></span>
        <Meter value={summary.quoteFreshness||0} max={100} warnAt={92} critAt={80}/>
      </div>
      <div className="strip-cell">
        <span className="strip-label">Depth ready · {subCount} ch</span>
        <span className="strip-value">{(summary.depthReady||0).toFixed(1)}<span style={{color:'var(--text-3)', fontSize:12}}>%</span></span>
        <Meter value={summary.depthReady||0} max={100} warnAt={90} critAt={75}/>
      </div>
      <div className="strip-cell">
        <span className="strip-label">WD Free · last flush</span>
        <span className="strip-value small">
          {summary.wdFreeGb != null ? `${(summary.wdFreeGb / 1000).toFixed(2)}` : '—'}
          {summary.wdFreeGb != null && <span style={{color:'var(--text-3)', fontSize:12}}> TB</span>}
        </span>
        <span className="strip-sub">{summary.wdMountOk != null ? (summary.wdMountOk ? 'mount ok' : 'MOUNT ERR') : '—'}</span>
      </div>
    </div>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────
function Sidebar({ audioOn, onToggleAudio, depthSummary, wdHistory, storage, connected, session }) {
  const feedConnected = session?.feed_connected ?? false;
  const subCount      = session?.feed_subscribed_count ?? 0;
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
          {connected ? <div className="live-dot"/> : <span className="badge ghost">DISC</span>}
        </div>
        <div className="ws-card">
          <div className="label">
            <span className="ttl">Dhan Feed</span>
            <span className="sub">{subCount} sub · live</span>
          </div>
          <span className={`badge ${feedConnected ? 'ok' : 'ghost'}`}>
            <span className="dot"/>{feedConnected ? 'LIVE' : 'DISC'}
          </span>
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
            ['GET','get','/api/live/session'],
            ['GET','get','/api/live/positions'],
            ['GET','get','/api/live/equity-curve'],
            ['GET','get','/api/live/signal-log'],
            ['GET','get','/api/live/depth-health'],
            ['GET','get','/api/live/storage-health'],
            ['GET','get','/api/live/alerts'],
            ['GET','get','/api/live/option-chain/{sym}'],
            ['WS', 'ws', '/ws/live'],
          ].map(([m, mc, p]) => (
            <div className="api-row" key={p}>
              <span style={{display:'flex', alignItems:'center', gap:6, minWidth:0}}>
                <span className={`method ${mc}`}>{m}</span>
                <span className="path">{p}</span>
              </span>
              <span className={`lat ${connected ? 'pos' : ''}`}>{connected ? 'ok' : '—'}</span>
            </div>
          ))}
        </div>
      </div>
      <DepthHealthPanel depthSummary={depthSummary} />
      <StoragePanel wdHistory={wdHistory} storage={storage} />
    </aside>
  );
}

// ── App ───────────────────────────────────────────────────────────────────
function App() {
  const [tweaks, setTweak] = useTweaks(/*EDITMODE-BEGIN*/{
    "brandName": "Strategy Monitor",
    "brandSub":  "NSE · LIVE PAPER",
    "accentColor": "#4ade80",
    "showGross": false,
  }/*EDITMODE-END*/);

  // ── State — all start empty, filled exclusively from API ──────────────
  const [positions,  setPositions]  = useState([]);
  const [equity,     setEquity]     = useState([]);
  const [signalLog,  setSignalLog]  = useState([]);
  const [alerts,     setAlerts]     = useState([]);
  const [session,    setSession]    = useState(null);
  const [storage,    setStorage]    = useState(null);
  const [connected,  setConnected]  = useState(false);
  const [audioOn,    setAudioOn]    = useState(true);
  const [wdHistory,  setWdHistory]  = useState([]);

  // Spot data — starts empty, replaced by API data on mount
  const [spotData, setSpotData] = useState({
    NIFTY: [], FINNIFTY: [], MIDCPNIFTY: [], SENSEX: [],
  });

  // Option chain — fetched from /api/live/option-chain/{symbol}
  const [chainData, setChainData] = useState({
    NIFTY: [], FINNIFTY: [], MIDCPNIFTY: [], SENSEX: [],
  });
  const [chainLoaded, setChainLoaded] = useState(false);

  const seriesRef = useRef(null);

  // ── Initial REST fetch ─────────────────────────────────────────────────
  useEffect(() => {
    const getJson = url =>
      fetch(url, {headers:{'Accept':'application/json'}})
        .then(r => r.ok ? r.json() : null)
        .catch(() => null);

    Promise.all([
      getJson('/api/live/session'),
      getJson('/api/live/positions'),
      getJson('/api/live/equity-curve'),
      getJson('/api/live/signal-log'),
      getJson('/api/live/alerts'),
    ]).then(([sess, pos, eq, sig, al]) => {
      if (sess) setSession(sess);

      // Always replace state with API response (even if empty array)
      setPositions(Array.isArray(pos) ? pos.map(adaptPosition) : []);

      if (Array.isArray(eq) && eq.length) {
        const pts = eq.map(adaptEquityPoint);
        setEquity(pts);
        window.__pendingEquityData = pts;
      }

      if (Array.isArray(sig)) setSignalLog(sig.map(adaptSignalLog));

      if (al?.active_alerts) setAlerts(al.active_alerts.map(adaptAlert));
    });

    // Spot bars
    Promise.all(
      ['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'].map(sym =>
        getJson(`/api/live/spot/${sym}`)
      )
    ).then(([n, f, m, s]) => {
      setSpotData({
        NIFTY:      Array.isArray(n) ? n : [],
        FINNIFTY:   Array.isArray(f) ? f : [],
        MIDCPNIFTY: Array.isArray(m) ? m : [],
        SENSEX:     Array.isArray(s) ? s : [],
      });
    });

    // Option chain — initial fetch
    fetchChainData(getJson);
  }, []);

  function fetchChainData(getJsonFn) {
    const getJson = getJsonFn || (url =>
      fetch(url, {headers:{'Accept':'application/json'}})
        .then(r => r.ok ? r.json() : null)
        .catch(() => null));

    Promise.all(
      ['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX'].map(sym =>
        getJson(`/api/live/option-chain/${sym}`)
      )
    ).then(([n, f, m, s]) => {
      setChainData({
        NIFTY:      Array.isArray(n) ? n : [],
        FINNIFTY:   Array.isArray(f) ? f : [],
        MIDCPNIFTY: Array.isArray(m) ? m : [],
        SENSEX:     Array.isArray(s) ? s : [],
      });
      setChainLoaded(true);
    });
  }

  // Poll option chain every 5s (live quotes refresh)
  useEffect(() => {
    const id = setInterval(() => fetchChainData(), 5000);
    return () => clearInterval(id);
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/ws/live`;
    let ws = null, timer = null, alive = true;

    function connect() {
      if (!alive) return;
      try { ws = new WebSocket(wsUrl); } catch(e) { schedule(); return; }

      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); schedule(); };
      ws.onerror = () => { try { ws.close(); } catch(e) {} };

      ws.onmessage = ev => {
        try {
          const f = JSON.parse(ev.data);

          if (f.session) setSession(f.session);

          if (f.positions !== undefined) {
            setPositions(Array.isArray(f.positions) ? f.positions.map(adaptPosition) : []);
          }

          if (f.equity_tick) {
            const pt = adaptEquityPoint(f.equity_tick);
            if (seriesRef.current) {
              try { seriesRef.current.update({ time: pt.time, value: pt.cumulative_net_pnl }); }
              catch(e) {}
            }
            setEquity(prev => {
              const arr = [...prev];
              if (!arr.length || arr[arr.length - 1].time < pt.time) arr.push(pt);
              else arr[arr.length - 1] = pt;
              return arr;
            });
          }

          if (f.storage) {
            setStorage(f.storage);
            if (f.storage.wd_free_gb != null) {
              setWdHistory(prev => [...prev.slice(-80), f.storage.wd_free_gb]);
            }
          }

          if (f.alerts?.active_alerts) {
            setAlerts(f.alerts.active_alerts.map(adaptAlert));
          }
        } catch(e) {}
      };
    }

    function schedule() { if (alive) timer = setTimeout(connect, 3000); }
    connect();
    return () => { alive = false; clearTimeout(timer); try { ws?.close(); } catch(e) {} };
  }, []);

  // ── Summary for HeaderStrip ────────────────────────────────────────────
  const lastEq    = equity[equity.length - 1] || {};
  const peakGross = equity.reduce((m, p) => Math.max(m, p.cumulative_gross_pnl ?? 0), 0);

  const quoteFreshness = session?.quote_freshness_pct ?? 0;
  const depthReady     = session?.depth?.ready_pct ?? 0;
  const marketClosed   = !session || session.market_status === 'CLOSED' || session.market_status === 'HOLIDAY';

  const summary = {
    gross:          lastEq.cumulative_gross_pnl ?? 0,
    net:            lastEq.cumulative_net_pnl   ?? 0,
    peakGross,
    quoteFreshness,
    depthReady,
    wdFreeGb:  storage?.wd_free_gb  ?? null,
    wdMountOk: storage?.wd_mount_ok ?? null,
  };

  // DepthSummary from session.depth (aggregate stats only)
  const depthSummary = session?.depth ?? null;

  return (
    <div data-screen-label="01 Live Monitor">
      <TopBar activeTab="live" onTab={() => {}}
        brandName={tweaks.brandName} brandSub={tweaks.brandSub} />
      <HeaderStrip summary={summary} session={session} />
      <div className="shell">
        <div className="workspace">
          <PositionsPanel positions={positions} />
          <EquityCurvePanel seriesRef={seriesRef} equityState={equity}
            accentColor={tweaks.accentColor} />
          <SpotChartsRow spotData={spotData} marketClosed={marketClosed} />
          <OptionChainPanel chainData={chainData} chainLoaded={chainLoaded} marketClosed={marketClosed} session={session} />
          <SignalLogPanel entries={signalLog} />
          <AlertsPanel alerts={alerts} />
        </div>
        <Sidebar
          audioOn={audioOn}
          onToggleAudio={() => setAudioOn(v => !v)}
          depthSummary={depthSummary}
          wdHistory={wdHistory}
          storage={storage}
          connected={connected}
          session={session}
        />
      </div>
      <TweaksPanel tweaks={tweaks} setTweak={setTweak}>
        <TweakSection label="Branding">
          <TweakText label="Dashboard name" tweakKey="brandName" tweaks={tweaks} setTweak={setTweak} />
          <TweakText label="Sub-label"      tweakKey="brandSub"  tweaks={tweaks} setTweak={setTweak} />
        </TweakSection>
        <TweakSection label="Chart">
          <TweakColor label="Equity curve colour" tweakKey="accentColor" tweaks={tweaks} setTweak={setTweak}
            options={['#4ade80','#38bdf8','#a78bfa','#fb923c']} />
          <TweakToggle label="Show Gross P&L" tweakKey="showGross" tweaks={tweaks} setTweak={setTweak} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App/>);
