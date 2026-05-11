// App shell — Live Monitor tab

const { fmtINR, fmtNum, fmtPct, INITIAL_POSITIONS, generateEquitySeed, SIGNAL_LOG_SEED, DEPTH_HEALTH_SEED, ALERTS_SEED, generateFrame, computePnL } = window.WingData;

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
