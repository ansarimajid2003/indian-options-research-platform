// Historical Explorer — read-only historical data viewer (v2)

const { useState, useEffect, useRef, useMemo } = React;
const { fmtINR, fmtNum, fmtPct, ist, aggregateBars } = window.WingData;

const DATASETS = [
  { key: 'SPOT', label: 'SPOT' },
  { key: 'VIX', label: 'INDIA VIX' },
  { key: 'OPTIONS', label: 'OPTIONS 1-MIN' },
  { key: 'BHAVCOPY', label: 'BHAVCOPY EOD' },
];

const SYMBOLS = ['NIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKNIFTY'];

const TF_OPTIONS = [
  { key: '1m', minutes: 1 },
  { key: '5m', minutes: 5 },
  { key: '15m', minutes: 15 },
  { key: '1H', minutes: 60 },
  { key: '1D', minutes: 390 },
];

function _today() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function _daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function _toChartTime(ts) {
  return Math.floor(new Date(ts).getTime() / 1000);
}

function _buildApiUrl(dataset, symbol, start, end, tf, expiryType, atmOffset, optType) {
  if (dataset === 'SPOT') return `/api/historical/spot/${encodeURIComponent(symbol)}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&tf=${tf}`;
  if (dataset === 'VIX') return `/api/historical/vix?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&tf=${tf}`;
  if (dataset === 'OPTIONS') return `/api/historical/options/${encodeURIComponent(symbol)}?expiry=${encodeURIComponent(expiryType)}&strike=${encodeURIComponent(atmOffset)}&opt_type=${encodeURIComponent(optType)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  if (dataset === 'BHAVCOPY') return `/api/historical/bhavcopy/${encodeURIComponent(symbol)}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  return '';
}

function _barToCandle(bar) {
  return {
    time: _toChartTime(bar.ts),
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  };
}

function _barToVolume(bar) {
  return {
    time: _toChartTime(bar.ts),
    value: bar.volume || 0,
    color: bar.close >= bar.open ? 'rgba(74,222,128,0.35)' : 'rgba(248,113,113,0.35)',
  };
}

function _barToOI(bar) {
  return {
    time: _toChartTime(bar.ts),
    value: bar.oi || 0,
  };
}

function exportCSV(bars, dataset) {
  if (!bars || !bars.length) return;
  let cols, headers;
  if (dataset === 'BHAVCOPY') {
    cols = ['ts','expiry','strike','option_type','open','high','low','close','settle_price','oi','volume'];
    headers = ['DATE','EXPIRY','STRIKE','TYPE','OPEN','HIGH','LOW','CLOSE','SETTLE','OI','VOLUME'];
  } else if (dataset === 'OPTIONS') {
    cols = ['ts','open','high','low','close','volume','oi','iv_clean','strike','spot'];
    headers = ['TIME','OPEN','HIGH','LOW','CLOSE','VOLUME','OI','IV','STRIKE','SPOT'];
  } else {
    cols = ['ts','open','high','low','close','volume'];
    headers = ['TIME','OPEN','HIGH','LOW','CLOSE','VOLUME'];
  }
  const rows = bars.map(b => cols.map(c => {
    const v = b[c];
    if (v == null) return '';
    if (typeof v === 'string' && v.includes(',')) return `"${v}"`;
    return v;
  }).join(','));
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `historical_${dataset.toLowerCase()}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function HistoricalTab({ symbol: defaultSymbol }) {
  const [dataset, setDataset] = useState('SPOT');
  const [symbol, setSymbol] = useState(defaultSymbol || 'NIFTY');
  const [startDate, setStartDate] = useState(_daysAgo(30));
  const [endDate, setEndDate] = useState(_today());
  const [tf, setTf] = useState('1m');
  const [expiryType, setExpiryType] = useState('week');
  const [atmOffset, setAtmOffset] = useState('ATM');
  const [optType, setOptType] = useState('CE');
  const [metadata, setMetadata] = useState(null);
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const chartContainerRef = useRef(null);
  const tooltipRef = useRef(null);
  const chartRef = useRef(null);
  const mainSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const oiSeriesRef = useRef(null);
  const datasetRef = useRef(dataset);
  datasetRef.current = dataset;

  // Fetch options metadata when symbol changes while on OPTIONS
  useEffect(() => {
    if (dataset !== 'OPTIONS') return;
    setMetadata(null);
    fetch(`/api/historical/metadata/${encodeURIComponent(symbol)}`, { headers: { 'Accept': 'application/json' } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setMetadata(data);
          if (data.expiry_types && data.expiry_types.length) setExpiryType(prev => data.expiry_types.includes(prev) ? prev : data.expiry_types[0]);
          if (data.atm_offsets && data.atm_offsets.length) setAtmOffset(prev => data.atm_offsets.includes(prev) ? prev : data.atm_offsets.find(x => x === 'ATM') || data.atm_offsets[0]);
          if (data.opt_types && data.opt_types.length) setOptType(prev => data.opt_types.includes(prev) ? prev : data.opt_types[0]);
        }
      })
      .catch(() => {});
  }, [symbol, dataset]);

  // Create chart once
  useEffect(() => {
    if (!chartContainerRef.current || !window.LightweightCharts) return;
    const chart = window.LightweightCharts.createChart(chartContainerRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#0a0a0a' }, textColor: '#6e6e6e', fontFamily: 'JetBrains Mono', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1, vertLine: { color: 'rgba(255,255,255,0.15)', width: 1, style: 3 }, horzLine: { color: 'rgba(255,255,255,0.15)', width: 1, style: 3 } },
      handleScale: true, handleScroll: true,
    });
    chartRef.current = chart;

    const tooltip = tooltipRef.current;
    chart.subscribeCrosshairMove(param => {
      if (!tooltip) return;
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0 || !param.seriesData || param.seriesData.size === 0) {
        tooltip.style.display = 'none';
        return;
      }
      const series = mainSeriesRef.current;
      if (!series) { tooltip.style.display = 'none'; return; }
      const data = param.seriesData.get(series);
      if (!data) { tooltip.style.display = 'none'; return; }

      const rect = chartContainerRef.current.getBoundingClientRect();
      let left = param.point.x + 12;
      let top = param.point.y + 12;
      if (left + 150 > rect.width) left = param.point.x - 162;
      if (top + 90 > rect.height) top = param.point.y - 102;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
      tooltip.style.display = 'block';

      const ds = datasetRef.current;
      const tStr = new Date(data.time * 1000).toLocaleString('en-IN', { hour12: false, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      if (ds === 'VIX') {
        tooltip.innerHTML = `<div style="font-size:10px;color:#a3a3a3;margin-bottom:4px">${tStr}</div>
          <div style="font-family:JetBrains Mono,monospace;font-size:12px;color:var(--cyan)">${fmtNum(data.value, 2)}</div>`;
      } else {
        const o = data.open, h = data.high, l = data.low, c = data.close;
        tooltip.innerHTML = `<div style="font-size:10px;color:#a3a3a3;margin-bottom:3px">${tStr}</div>
          <div style="display:grid;grid-template-columns:auto auto;gap:4px 10px;font-size:11px;font-family:JetBrains Mono,monospace">
            <span style="color:var(--text-3)">O</span><span style="color:var(--text)">${fmtNum(o, 2)}</span>
            <span style="color:var(--text-3)">H</span><span style="color:var(--text)">${fmtNum(h, 2)}</span>
            <span style="color:var(--text-3)">L</span><span style="color:var(--text)">${fmtNum(l, 2)}</span>
            <span style="color:var(--text-3)">C</span><span style="color:${c >= o ? 'var(--green)' : 'var(--red)'}">${fmtNum(c, 2)}</span>
          </div>`;
      }
    });

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth, height: chartContainerRef.current.clientHeight });
      }
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      mainSeriesRef.current = null;
      volumeSeriesRef.current = null;
      oiSeriesRef.current = null;
    };
  }, []);

  // Update chart series when response changes
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (mainSeriesRef.current) { try { chart.removeSeries(mainSeriesRef.current); } catch(e) {} mainSeriesRef.current = null; }
    if (volumeSeriesRef.current) { try { chart.removeSeries(volumeSeriesRef.current); } catch(e) {} volumeSeriesRef.current = null; }
    if (oiSeriesRef.current) { try { chart.removeSeries(oiSeriesRef.current); } catch(e) {} oiSeriesRef.current = null; }

    if (!response || !response.bars || !response.bars.length) return;

    const bars = response.bars;
    const isVIX = datasetRef.current === 'VIX';
    const isOptions = datasetRef.current === 'OPTIONS';

    if (isVIX) {
      const series = chart.addLineSeries({
        color: '#38bdf8',
        lineWidth: 1.5,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      });
      series.setData(bars.map(b => ({ time: _toChartTime(b.ts), value: b.close })));
      mainSeriesRef.current = series;
    } else {
      const series = chart.addCandlestickSeries({
        upColor: '#4ade80', downColor: '#f87171',
        borderUpColor: '#4ade80', borderDownColor: '#f87171',
        wickUpColor: 'rgba(74,222,128,0.5)', wickDownColor: 'rgba(248,113,113,0.5)',
      });
      series.setData(bars.map(_barToCandle));
      mainSeriesRef.current = series;

      const volSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: '',
      });
      volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
      volSeries.setData(bars.map(_barToVolume));
      volumeSeriesRef.current = volSeries;

      if (isOptions && bars.some(b => b.oi != null)) {
        const oiSeries = chart.addLineSeries({
          color: '#a78bfa',
          lineWidth: 1,
          priceScaleId: 'oi',
        });
        oiSeries.priceScale().applyOptions({ scaleMargins: { top: 0.65, bottom: 0.15 } });
        oiSeries.setData(bars.map(_barToOI).filter(b => b.value != null));
        oiSeriesRef.current = oiSeries;
      }
    }

    setTimeout(() => chart.timeScale().fitContent(), 60);
  }, [response]);

  async function handleLoad() {
    setLoading(true);
    setError(null);
    setResponse(null);
    const tfMin = TF_OPTIONS.find(t => t.key === tf)?.minutes || 1;
    const url = _buildApiUrl(dataset, symbol, startDate, endDate, tfMin, expiryType, atmOffset, optType);
    try {
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) {
        const text = await res.text().catch(() => 'Error');
        setError(`${res.status}: ${text}`);
        setLoading(false);
        return;
      }
      const data = await res.json();
      setResponse(data);
    } catch (e) {
      setError(String(e.message || e));
    }
    setLoading(false);
  }

  function handleClear() {
    setResponse(null);
    setError(null);
    const chart = chartRef.current;
    if (chart) {
      if (mainSeriesRef.current) { try { chart.removeSeries(mainSeriesRef.current); } catch(e) {} mainSeriesRef.current = null; }
      if (volumeSeriesRef.current) { try { chart.removeSeries(volumeSeriesRef.current); } catch(e) {} volumeSeriesRef.current = null; }
      if (oiSeriesRef.current) { try { chart.removeSeries(oiSeriesRef.current); } catch(e) {} oiSeriesRef.current = null; }
    }
  }

  const stats = response?.stats || {};
  const bars = response?.bars || [];
  const showSymbol = dataset !== 'VIX';
  const showTf = dataset !== 'BHAVCOPY';
  const showOptions = dataset === 'OPTIONS';

  const isBhavcopy = dataset === 'BHAVCOPY';
  const isOptionsTable = dataset === 'OPTIONS';

  const tableHeaders = isBhavcopy
    ? ['DATE','EXPIRY','STRIKE','TYPE','OPEN','HIGH','LOW','CLOSE','SETTLE','OI','VOLUME']
    : isOptionsTable
    ? ['TIME','OPEN','HIGH','LOW','CLOSE','VOLUME','OI','IV','STRIKE','SPOT']
    : ['TIME','OPEN','HIGH','LOW','CLOSE','VOLUME'];

  const displayRows = bars.slice(0, 500);

  return (
    <div className="hist-shell">
      {/* Control bar */}
      <div style={{ position: 'sticky', top: 48, zIndex: 9, background: 'var(--bg)', borderBottom: '1px solid var(--border)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', height: 48 }}>
        <select className="ctrl-select" value={dataset} onChange={e => setDataset(e.target.value)} style={{ width: 130 }}>
          {DATASETS.map(d => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        {showSymbol && (
          <select className="ctrl-select" value={symbol} onChange={e => setSymbol(e.target.value)} style={{ width: 110 }}>
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <input className="ctrl-date" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={{ width: 130 }} />
        <input className="ctrl-date" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={{ width: 130 }} />
        {showTf && (
          <select className="ctrl-select" value={tf} onChange={e => setTf(e.target.value)} style={{ width: 70 }}>
            {TF_OPTIONS.map(t => <option key={t.key} value={t.key}>{t.key}</option>)}
          </select>
        )}
        {showOptions && (
          <>
            <select className="ctrl-select" value={expiryType} onChange={e => setExpiryType(e.target.value)} style={{ width: 90 }}>
              {(metadata?.expiry_types || ['week','month']).map(e => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="ctrl-select" value={atmOffset} onChange={e => setAtmOffset(e.target.value)} style={{ width: 100 }}>
              {(metadata?.atm_offsets || ['ATM']).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            <select className="ctrl-select" value={optType} onChange={e => setOptType(e.target.value)} style={{ width: 60 }}>
              {(metadata?.opt_types || ['CE','PE']).map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </>
        )}
        <button className="ctrl-btn primary" onClick={handleLoad} disabled={loading}>{loading ? 'LOADING…' : 'LOAD'}</button>
        <button className="ctrl-btn" onClick={handleClear} disabled={loading}>CLEAR</button>
        {response?.truncated && <span className="badge warning">TRUNCATED</span>}
        {error && <span className="badge critical" style={{ marginLeft: 'auto' }}>{error}</span>}
      </div>

      {/* Chart panel */}
      <div className="hist-chart-panel panel" style={{ borderLeft: 'none', borderRight: 'none' }}>
        <div className="panel-header">
          <div className="panel-title">
            Chart
            <span className="count">{response ? `${response.row_count || 0} BARS · ${response.source || ''}` : 'NO DATA'}</span>
          </div>
          <div className="panel-actions">
            {response?.data_age_s != null && <span className="badge ghost">{fmtNum(response.data_age_s, 0)}s AGE</span>}
          </div>
        </div>
        <div style={{ position: 'relative', height: 480 }}>
          <div ref={chartContainerRef} style={{ position: 'absolute', inset: 0 }} />
          <div ref={tooltipRef} style={{ position: 'absolute', display: 'none', background: '#161616', border: '1px solid var(--border-strong)', padding: '8px 10px', pointerEvents: 'none', zIndex: 20, minWidth: 120 }} />
          {!response && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
              <span style={{ fontSize: 12, color: 'var(--text-3)', letterSpacing: '0.06em' }}>SELECT A DATASET AND CLICK LOAD</span>
            </div>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div className="hist-stats">
        <div className="strip-cell">
          <span className="strip-label">Period</span>
          <span className="strip-value small">{startDate} <span style={{color:'var(--text-3)'}}>→</span> {endDate}</span>
          <span className="strip-sub muted">{bars.length ? `${bars.length} rows` : '—'}</span>
        </div>
        <div className="strip-cell">
          <span className="strip-label">Bars</span>
          <span className="strip-value">{response?.row_count ?? '—'}</span>
          <span className="strip-sub muted">{dataset === 'BHAVCOPY' ? 'EOD' : tf}</span>
        </div>
        <div className="strip-cell">
          <span className="strip-label">Mean Close</span>
          <span className="strip-value">{stats.mean_close != null ? fmtNum(stats.mean_close, 2) : '—'}</span>
          <span className="strip-sub muted">σ {stats.std_close != null ? fmtNum(stats.std_close, 2) : '—'}</span>
        </div>
        <div className="strip-cell">
          <span className="strip-label">Range H-L</span>
          <span className="strip-value">{stats.max_close != null && stats.min_close != null ? fmtNum(stats.max_close - stats.min_close, 2) : '—'}</span>
          <span className="strip-sub muted">{stats.max_close != null ? fmtNum(stats.max_close, 2) : '—'} · {stats.min_close != null ? fmtNum(stats.min_close, 2) : '—'}</span>
        </div>
        <div className="strip-cell">
          <span className="strip-label">Volume Total</span>
          <span className="strip-value">{stats.total_volume != null ? fmtNum(stats.total_volume, 0) : '—'}</span>
          <span className="strip-sub muted">{bars.length && stats.total_volume != null ? `avg ${fmtNum(stats.total_volume / bars.length, 0)}` : '—'}</span>
        </div>
      </div>

      {/* Data table */}
      <div className="panel" style={{ borderLeft: 'none', borderRight: 'none', borderBottom: '1px solid var(--border)', maxHeight: 400, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div className="panel-header">
          <div className="panel-title">Data <span className="count">{bars.length} ROWS</span></div>
          <div className="panel-actions">
            <button className="ctrl-btn" onClick={() => exportCSV(bars, dataset)} disabled={!bars.length}><Icon name="export" size={11}/> CSV</button>
          </div>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {bars.length === 0 ? (
            <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-3)', fontSize: 12, letterSpacing: '0.06em' }}>NO DATA</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  {tableHeaders.map(h => <th key={h} className={['DATE','TIME','EXPIRY','TYPE'].includes(h) ? 'l' : ''}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {displayRows.map((b, i) => (
                  <tr key={i}>
                    {isBhavcopy ? (
                      <>
                        <td className="l muted">{b.ts?.slice(0,10) || '—'}</td>
                        <td className="l">{b.expiry || '—'}</td>
                        <td>{b.strike != null ? fmtNum(b.strike, 0) : '—'}</td>
                        <td className="l">{b.option_type || '—'}</td>
                        <td>{fmtNum(b.open, 2)}</td>
                        <td>{fmtNum(b.high, 2)}</td>
                        <td>{fmtNum(b.low, 2)}</td>
                        <td>{fmtNum(b.close, 2)}</td>
                        <td>{b.settle_price != null ? fmtNum(b.settle_price, 2) : '—'}</td>
                        <td>{b.oi != null ? fmtNum(b.oi, 0) : '—'}</td>
                        <td>{b.volume != null ? fmtNum(b.volume, 0) : '—'}</td>
                      </>
                    ) : isOptionsTable ? (
                      <>
                        <td className="l muted">{ist(b.ts)}</td>
                        <td>{fmtNum(b.open, 2)}</td>
                        <td>{fmtNum(b.high, 2)}</td>
                        <td>{fmtNum(b.low, 2)}</td>
                        <td>{fmtNum(b.close, 2)}</td>
                        <td>{b.volume != null ? fmtNum(b.volume, 0) : '—'}</td>
                        <td>{b.oi != null ? fmtNum(b.oi, 0) : '—'}</td>
                        <td>{b.iv_clean != null ? fmtNum(b.iv_clean, 2) : '—'}</td>
                        <td>{b.strike != null ? fmtNum(b.strike, 0) : '—'}</td>
                        <td>{b.spot != null ? fmtNum(b.spot, 2) : '—'}</td>
                      </>
                    ) : (
                      <>
                        <td className="l muted">{ist(b.ts)}</td>
                        <td>{fmtNum(b.open, 2)}</td>
                        <td>{fmtNum(b.high, 2)}</td>
                        <td>{fmtNum(b.low, 2)}</td>
                        <td>{fmtNum(b.close, 2)}</td>
                        <td>{b.volume != null ? fmtNum(b.volume, 0) : '—'}</td>
                      </>
                    )}
                  </tr>
                ))}
                {bars.length > 500 && (
                  <tr>
                    <td colSpan={tableHeaders.length} style={{ textAlign: 'center', color: 'var(--text-3)', fontSize: 11, padding: '10px 0' }}>
                      + {bars.length - 500} more rows · export CSV for full dataset
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { HistoricalTab });
