// Backtests comparison and drill-down (v2)

const { useState, useEffect, useRef, useMemo } = React;
const { fmtINR, fmtNum, fmtPct } = window.WingData;

const EQUITY_COLORS = [
  '#38bdf8',
  '#4ade80',
  '#fbbf24',
  '#a78bfa',
  '#f87171',
];

const SORT_OPTIONS = [
  ['date', 'DATE'],
  ['net_pnl', 'NET PNL'],
  ['sharpe', 'SHARPE'],
  ['sortino', 'SORTINO'],
  ['calmar', 'CALMAR'],
  ['max_dd_pct', 'MAX DD'],
  ['trades', 'TRADES'],
  ['name', 'NAME'],
  ['group', 'GROUP'],
];

const GROUP_OPTIONS = [
  ['group', 'VIRTUAL FOLDERS'],
  ['strategy', 'STRATEGY'],
  ['run', 'RUN'],
  ['none', 'FLAT'],
];

function _metricColor(value, type) {
  if (value == null) return { cls: '', style: {} };
  if (type === 'pnl' || type === 'cagr') {
    return value >= 0 ? { cls: 'pos', style: {} } : { cls: 'neg', style: {} };
  }
  if (type === 'sharpe' || type === 'sortino' || type === 'calmar') {
    if (value >= 2) return { cls: 'pos', style: {} };
    if (value >= 1) return { cls: '', style: { color: 'var(--amber)' } };
    return { cls: 'neg', style: {} };
  }
  if (type === 'maxdd') {
    const abs = Math.abs(value);
    if (abs < 2) return { cls: 'pos', style: {} };
    if (abs <= 5) return { cls: '', style: { color: 'var(--amber)' } };
    return { cls: 'neg', style: {} };
  }
  if (type === 'win') {
    if (value >= 60) return { cls: 'pos', style: {} };
    if (value >= 50) return { cls: '', style: { color: 'var(--amber)' } };
    return { cls: 'neg', style: {} };
  }
  if (type === 'tstat') {
    return value >= 2 ? { cls: 'pos', style: {} } : { cls: '', style: { color: 'var(--text-2)' } };
  }
  if (type === 'pf') {
    return value >= 1.5 ? { cls: 'pos', style: {} } : { cls: '', style: { color: 'var(--text-2)' } };
  }
  return { cls: '', style: {} };
}

function _fmtMetric(v, dp = 2) {
  if (v == null) return '—';
  return fmtNum(v, dp);
}

function _toEpochSeconds(value) {
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

function _uniqueTimePoints(points) {
  const byTime = new Map();
  points.forEach(point => {
    if (point.time == null || point.value == null || !Number.isFinite(point.value)) return;
    byTime.set(point.time, point);
  });
  return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
}

function _groupLabel(bt, groupBy) {
  if (groupBy === 'strategy') return (bt.strategy_family || 'misc').replace(/_/g, ' ').toUpperCase();
  if (groupBy === 'run') return bt.run_key || 'UNKNOWN RUN';
  if (groupBy === 'group') return bt.group_label || bt.group_path || 'UNGROUPED';
  return 'ALL RUNS';
}

function BacktestsTab() {
  const [backtests, setBacktests] = useState([]);
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [focusedId, setFocusedId] = useState(null);
  const [equityCurves, setEquityCurves] = useState({});
  const [drawdown, setDrawdown] = useState([]);
  const [monthly, setMonthly] = useState([]);
  const [ledger, setLedger] = useState(null);
  const [events, setEvents] = useState([]);
  const [decisions, setDecisions] = useState(null);
  const [equityMode, setEquityMode] = useState('absolute');
  const [ledgerFilterSymbol, setLedgerFilterSymbol] = useState('');
  const [ledgerFilterReason, setLedgerFilterReason] = useState('');
  const [ledgerPage, setLedgerPage] = useState(0);
  const [ledgerSize, setLedgerSize] = useState(25);
  const [showDecisions, setShowDecisions] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [sortBy, setSortBy] = useState('date');
  const [sortDir, setSortDir] = useState('desc');
  const [groupBy, setGroupBy] = useState('group');

  const equityChartRef = useRef(null);
  const drawdownChartRef = useRef(null);
  const equityChartInstance = useRef(null);
  const drawdownChartInstance = useRef(null);
  const equitySeriesMap = useRef(new Map());

  // Fetch backtest list whenever sort order changes.
  useEffect(() => {
    const url = `/api/backtests?sort_by=${encodeURIComponent(sortBy)}&sort_dir=${encodeURIComponent(sortDir)}`;
    fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && Array.isArray(data.backtests)) {
          setBacktests(data.backtests);
          const focusedStillExists = focusedId && data.backtests.some(b => b.id === focusedId);
          if (data.backtests.length && !focusedStillExists) {
            const firstChartable = data.backtests.find(b => b.has_equity || b.has_ledger) || data.backtests[0];
            setFocusedId(firstChartable.id);
            if (firstChartable.has_equity) setCheckedIds(new Set([firstChartable.id]));
          }
        }
      })
      .catch(() => {});
  }, [sortBy, sortDir]);

  // Fetch focused backtest details
  useEffect(() => {
    if (!focusedId) return;
    const focused = backtests.find(b => b.id === focusedId);
    if (focused && !focused.has_ledger && !focused.has_equity) {
      setDrawdown([]);
      setMonthly([]);
      setEvents([]);
      setDecisions(null);
      setLedger({ total: 0, page: 0, size: ledgerSize, rows: [] });
      setLoadingDetail(false);
      return;
    }
    setLoadingDetail(true);
    setShowDecisions(false);
    setDecisions(null);

    const id = focusedId;

    Promise.all([
      fetch(`/api/backtests/${encodeURIComponent(id)}/drawdown`, { headers: { 'Accept': 'application/json' } }).then(r => r.ok ? r.json() : []),
      fetch(`/api/backtests/${encodeURIComponent(id)}/monthly`, { headers: { 'Accept': 'application/json' } }).then(r => r.ok ? r.json() : []),
      fetch(`/api/backtests/${encodeURIComponent(id)}/events`, { headers: { 'Accept': 'application/json' } }).then(r => r.ok ? r.json() : []),
      fetch(`/api/backtests/${encodeURIComponent(id)}/decisions`, { headers: { 'Accept': 'application/json' } }).then(r => r.ok ? r.json() : null),
    ]).then(([dd, mo, ev, de]) => {
      setDrawdown(Array.isArray(dd) ? dd : []);
      setMonthly(Array.isArray(mo) ? mo : []);
      setEvents(Array.isArray(ev) ? ev : []);
      setDecisions(de || null);
      setLoadingDetail(false);
    }).catch(() => setLoadingDetail(false));

    // reset page when switching focused backtest; ledger effect will fetch
    setLedgerPage(0);
  }, [focusedId, backtests]);

  // Refetch ledger when pagination/filters change
  useEffect(() => {
    if (!focusedId) return;
    _fetchLedger(focusedId, ledgerPage, ledgerSize, ledgerFilterSymbol, ledgerFilterReason);
  }, [focusedId, ledgerPage, ledgerSize, ledgerFilterSymbol, ledgerFilterReason]);

  function _fetchLedger(id, page, size, symFilter, reasonFilter) {
    const focused = backtests.find(b => b.id === id);
    if (focused && !focused.has_ledger) {
      setLedger({ total: 0, page, size, rows: [] });
      return;
    }
    let url = `/api/backtests/${encodeURIComponent(id)}/ledger?page=${page}&size=${size}`;
    if (symFilter) url += `&symbol=${encodeURIComponent(symFilter)}`;
    if (reasonFilter) url += `&exit_reason=${encodeURIComponent(reasonFilter)}`;
    fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(r => r.ok ? r.json() : null)
      .then(data => setLedger(data || { total: 0, page: 0, size: 25, rows: [] }))
      .catch(() => setLedger({ total: 0, page: 0, size: 25, rows: [] }));
  }

  // Fetch equity curves for checked backtests
  useEffect(() => {
    checkedIds.forEach(id => {
      if (equityCurves[id]) return;
      fetch(`/api/backtests/${encodeURIComponent(id)}/equity-curve`, { headers: { 'Accept': 'application/json' } })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data) {
            setEquityCurves(prev => ({ ...prev, [id]: data }));
          }
        })
        .catch(() => {});
    });
  }, [checkedIds]);

  // Initialize equity chart
  useEffect(() => {
    if (!equityChartRef.current || !window.LightweightCharts) return;
    const chart = window.LightweightCharts.createChart(equityChartRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#0a0a0a' }, textColor: '#6e6e6e', fontFamily: 'JetBrains Mono', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: false },
      crosshair: { mode: 1, vertLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: 3 }, horzLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: 3 } },
      handleScale: true, handleScroll: true,
    });
    equityChartInstance.current = chart;
    const handleResize = () => chart.applyOptions({ width: equityChartRef.current.clientWidth, height: equityChartRef.current.clientHeight });
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      equityChartInstance.current = null;
      equitySeriesMap.current.clear();
    };
  }, []);

  // Initialize drawdown chart
  useEffect(() => {
    if (!drawdownChartRef.current || !window.LightweightCharts) return;
    const chart = window.LightweightCharts.createChart(drawdownChartRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: '#0a0a0a' }, textColor: '#6e6e6e', fontFamily: 'JetBrains Mono', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: false },
      crosshair: { mode: 1, vertLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: 3 }, horzLine: { color: 'rgba(255,255,255,0.1)', width: 1, style: 3 } },
      handleScale: true, handleScroll: true,
    });
    drawdownChartInstance.current = chart;
    const handleResize = () => chart.applyOptions({ width: drawdownChartRef.current.clientWidth, height: drawdownChartRef.current.clientHeight });
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      drawdownChartInstance.current = null;
    };
  }, []);

  // Update equity chart series
  useEffect(() => {
    const chart = equityChartInstance.current;
    if (!chart) return;

    equitySeriesMap.current.forEach(s => { try { chart.removeSeries(s); } catch(e) {} });
    equitySeriesMap.current.clear();

    if (!checkedIds.size) return;

    const ids = Array.from(checkedIds);
    ids.forEach((id, idx) => {
      const data = equityCurves[id];
      if (!data || !data.bars || !data.bars.length) return;
      const color = EQUITY_COLORS[idx % EQUITY_COLORS.length];
      const series = chart.addLineSeries({ color, lineWidth: 1.5, priceFormat: { type: 'price', precision: 0, minMove: 1 } });
      const isNorm = equityMode === 'normalised';
      let firstVal = null;
      const chartData = _uniqueTimePoints(data.bars.map(b => {
        const t = _toEpochSeconds(b.ts);
        const v = b.close;
        if (firstVal == null) firstVal = v || 1;
        return { time: t, value: isNorm ? (v / firstVal) * 100 : v };
      }));
      if (!chartData.length) return;
      series.setData(chartData);
      equitySeriesMap.current.set(id, series);
    });

    setTimeout(() => chart.timeScale().fitContent(), 60);
  }, [checkedIds, equityCurves, equityMode]);

  // Overlay event markers on focused backtest equity series
  useEffect(() => {
    if (!events.length || !focusedId || !equitySeriesMap.current.has(focusedId)) return;
    const series = equitySeriesMap.current.get(focusedId);
    const markers = events.map(ev => {
      const isTradeMarker = ev.event_type === 'entry' || ev.event_type === 'exit';
      const marker = {
        time: _toEpochSeconds(ev.ts),
        position: 'aboveBar',
        color: ev.severity === 'critical' ? '#f87171' : ev.severity === 'warning' ? '#fbbf24' : '#38bdf8',
        shape: ev.event_type === 'entry' ? 'arrowDown' : ev.event_type === 'exit' ? 'arrowUp' : 'circle',
      };
      if (!isTradeMarker) marker.text = ev.label || ev.event_type;
      return marker;
    }).filter(m => m.time != null);
    series.setMarkers(markers);
  }, [events, focusedId]);

  // Update drawdown chart
  useEffect(() => {
    const chart = drawdownChartInstance.current;
    if (!chart) return;
    if (drawdownChartInstance._series) {
      try { chart.removeSeries(drawdownChartInstance._series); } catch(e) {}
      drawdownChartInstance._series = null;
    }
    if (!drawdown.length) return;

    const series = chart.addAreaSeries({
      lineColor: '#f87171',
      topColor: 'rgba(248,113,113,0.25)',
      bottomColor: 'rgba(248,113,113,0.0)',
      lineWidth: 1.2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });
    drawdownChartInstance._series = series;
    const chartData = _uniqueTimePoints(drawdown.map(d => ({
      time: _toEpochSeconds(d.date),
      value: d.drawdown_pct,
    })));
    if (!chartData.length) return;
    series.setData(chartData);

    setTimeout(() => chart.timeScale().fitContent(), 60);
  }, [drawdown]);

  function toggleCheck(id) {
    const bt = backtests.find(b => b.id === id);
    if (bt && !bt.has_equity) return;
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function focusBacktest(bt) {
    if (!bt) return;
    setFocusedId(bt.id);
    if (bt.has_equity) {
      setCheckedIds(prev => {
        if (prev.has(bt.id)) return prev;
        return new Set([...prev, bt.id]);
      });
    }
  }

  const focusedBt = backtests.find(b => b.id === focusedId) || null;
  const isSummaryOnly = focusedBt && (!focusedBt.has_ledger && !focusedBt.has_equity);

  const filteredBacktests = useMemo(() => {
    const q = sidebarQuery.trim().toLowerCase();
    const filtered = q
      ? backtests.filter(b =>
          (b.name || b.id).toLowerCase().includes(q) ||
          (b.symbol || '').toLowerCase().includes(q) ||
          (b.group_path || '').toLowerCase().includes(q) ||
          (b.strategy_family || '').toLowerCase().includes(q)
        )
      : backtests;
    return filtered.slice(0, 100);
  }, [backtests, sidebarQuery]);

  const groupedBacktests = useMemo(() => {
    if (groupBy === 'none') return [{ key: 'all', label: 'ALL RUNS', items: filteredBacktests }];
    const groups = [];
    const byKey = new Map();
    filteredBacktests.forEach(bt => {
      const key = groupBy === 'strategy'
        ? (bt.strategy_family || 'misc')
        : groupBy === 'run'
        ? (bt.run_key || 'unknown')
        : (bt.group_path || 'ungrouped');
      if (!byKey.has(key)) {
        const group = { key, label: _groupLabel(bt, groupBy), items: [] };
        byKey.set(key, group);
        groups.push(group);
      }
      byKey.get(key).items.push(bt);
    });
    return groups;
  }, [filteredBacktests, groupBy]);

  // Ledger exit reasons for filter dropdown
  const exitReasons = useMemo(() => {
    if (!ledger || !ledger.rows) return [];
    const set = new Set();
    ledger.rows.forEach(r => { if (r.exit_reason) set.add(r.exit_reason); });
    return Array.from(set).sort();
  }, [ledger]);

  // Monthly heatmap data
  const heatmapYears = useMemo(() => {
    if (!monthly.length) return [];
    const map = {};
    monthly.forEach(m => {
      if (!map[m.year]) map[m.year] = {};
      map[m.year][m.month] = m;
    });
    return Object.keys(map).sort().map(y => ({ year: parseInt(y), months: map[y] }));
  }, [monthly]);

  const monthLabels = ['J','F','M','A','M','J','J','A','S','O','N','D'];

  // PNL distribution from ledger
  const pnlDist = useMemo(() => {
    if (!ledger || !ledger.rows.length) return [];
    const values = ledger.rows.map(r => r.net_pnl || 0);
    if (!values.length) return [];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const bins = 12;
    const step = (max - min) / bins || 1;
    const counts = new Array(bins).fill(0);
    values.forEach(v => {
      const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - min) / step)));
      counts[idx]++;
    });
    const maxCount = Math.max(...counts, 1);
    return counts.map((c, i) => ({
      low: min + i * step,
      high: min + (i + 1) * step,
      count: c,
      pct: (c / values.length) * 100,
      height: (c / maxCount) * 100,
      color: (min + i * step) >= 0 ? 'var(--green)' : 'var(--red)',
    }));
  }, [ledger]);

  // Exit reason breakdown
  const exitBreakdown = useMemo(() => {
    if (!ledger || !ledger.rows.length) return [];
    const map = {};
    ledger.rows.forEach(r => {
      const reason = r.exit_reason || 'UNKNOWN';
      map[reason] = (map[reason] || 0) + 1;
    });
    const total = ledger.rows.length;
    const items = Object.entries(map).map(([reason, count]) => ({
      reason, count, pct: (count / total) * 100,
      pnl: ledger.rows.filter(r => r.exit_reason === reason).reduce((s, r) => s + (r.net_pnl || 0), 0),
    }));
    return items.sort((a, b) => b.count - a.count);
  }, [ledger]);

  return (
    <div className="bt-wrap">
      {/* Sidebar */}
      <div className="bt-sidebar">
        <div className="panel-header" style={{ borderLeft: 'none', borderRight: 'none', borderTop: 'none' }}>
          <div className="panel-title">Runs <span className="count">{backtests.length}</span></div>
        </div>
        <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)' }}>
          <input
            className="cmd-input"
            style={{ width: '100%', boxSizing: 'border-box' }}
            placeholder="FILTER BY NAME / SYMBOL / GROUP"
            value={sidebarQuery}
            onChange={e => setSidebarQuery(e.target.value)}
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 72px', gap: 6, marginTop: 6 }}>
            <select className="ctrl-select" value={sortBy} onChange={e => setSortBy(e.target.value)} style={{ width: '100%' }}>
              {SORT_OPTIONS.map(([key, label]) => <option key={key} value={key}>SORT {label}</option>)}
            </select>
            <button className="ctrl-btn" onClick={() => setSortDir(d => d === 'desc' ? 'asc' : 'desc')}>
              {sortDir === 'desc' ? 'DESC' : 'ASC'}
            </button>
          </div>
          <select className="ctrl-select" value={groupBy} onChange={e => setGroupBy(e.target.value)} style={{ width: '100%', marginTop: 6 }}>
            {GROUP_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {groupedBacktests.map(group => (
            <React.Fragment key={group.key}>
              <div style={{ padding: '7px 14px 5px', borderBottom: '1px solid var(--border)', color: 'var(--text-3)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.06em', background: 'rgba(255,255,255,0.018)', display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{group.label}</span>
                <span>{group.items.length}</span>
              </div>
              {group.items.map(bt => (
            <div key={bt.id} className={`bt-item ${focusedId === bt.id ? 'focused' : ''}`} onClick={() => focusBacktest(bt)}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11.5, fontWeight: 600, color: 'var(--text)' }}>{bt.name || bt.id}</span>
                <input
                  type="checkbox"
                  checked={checkedIds.has(bt.id)}
                  disabled={!bt.has_equity}
                  title={bt.has_equity ? 'Compare equity curve' : 'No equity curve for summary-only run'}
                  onClick={e => { e.stopPropagation(); toggleCheck(bt.id); }}
                  style={{ cursor: bt.has_equity ? 'pointer' : 'not-allowed', opacity: bt.has_equity ? 1 : 0.35 }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8, fontSize: 10, color: 'var(--text-3)', fontFamily: 'JetBrains Mono, monospace' }}>
                <span>{bt.symbol || '—'}</span>
                <span>{bt.start_date || ''} → {bt.end_date || ''}</span>
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 2, flexWrap: 'wrap' }}>
                {bt.legacy && <span className="badge ghost">LEGACY</span>}
                {bt.group_path && <span className="badge ghost">{bt.group_path}</span>}
                {!bt.has_ledger && !bt.has_equity && <span className="badge warning">SUMMARY ONLY</span>}
              </div>
            </div>
              ))}
            </React.Fragment>
          ))}
          {filteredBacktests.length === 100 && (
            <div style={{ padding: '8px 14px', fontSize: 10, color: 'var(--text-4)', fontFamily: 'JetBrains Mono, monospace' }}>
              SHOWING 100 OF {backtests.length} — USE FILTER TO NARROW
            </div>
          )}
        </div>
      </div>

      {/* Main */}
      <div className="bt-main">
        {/* Metrics table */}
        <div className="panel bt-metrics-panel">
          <div className="panel-header">
            <div className="panel-title">Metrics <span className="count">{filteredBacktests.length} SHOWN · {backtests.length} TOTAL</span></div>
          </div>
          <div className="bt-metrics-scroll">
            <table className="tbl" style={{ minWidth: 1180 }}>
              <thead>
                <tr>
                  <th className="l">NAME</th>
                  <th className="l">GROUP</th>
                  <th>SYMBOL</th>
                  <th>PERIOD</th>
                  <th>TRADES</th>
                  <th>NET PNL</th>
                  <th>CAGR</th>
                  <th>SHARPE</th>
                  <th>SORTINO</th>
                  <th>CALMAR</th>
                  <th>MAX DD%</th>
                  <th>WIN%</th>
                  <th>PF</th>
                  <th>T-STAT</th>
                </tr>
              </thead>
              <tbody>
                {filteredBacktests.map(bt => {
                  const pnlCol = _metricColor(bt.net_pnl, 'pnl');
                  const cagrCol = _metricColor(bt.cagr, 'cagr');
                  const sharpeCol = _metricColor(bt.sharpe, 'sharpe');
                  const sortinoCol = _metricColor(bt.sortino, 'sortino');
                  const calmarCol = _metricColor(bt.calmar, 'calmar');
                  const ddCol = _metricColor(bt.max_dd_pct, 'maxdd');
                  const winCol = _metricColor(bt.win_rate, 'win');
                  const tstatCol = _metricColor(bt.t_stat, 'tstat');
                  const pfCol = _metricColor(bt.profit_factor, 'pf');
                  return (
                    <tr key={bt.id} onClick={() => focusBacktest(bt)} style={{ cursor: 'pointer', borderLeft: focusedId === bt.id ? '2px solid var(--cyan)' : '2px solid transparent' }}>
                      <td className="l" style={{ fontWeight: focusedId === bt.id ? 600 : 400 }}>
                        {bt.name || bt.id}
                        {bt.legacy && <span className="badge ghost" style={{ marginLeft: 6, fontSize: 9 }}>LEGACY</span>}
                      </td>
                      <td className="l">{bt.group_path || '---'}</td>
                      <td>{bt.symbol || '—'}</td>
                      <td>{bt.start_date || ''} → {bt.end_date || ''}</td>
                      <td>{bt.trades}</td>
                      <td className={pnlCol.cls} style={pnlCol.style}>{fmtINR(bt.net_pnl, { dp: 0 })}</td>
                      <td className={cagrCol.cls} style={cagrCol.style}>{bt.cagr != null ? fmtNum(bt.cagr, 2) + '%' : '—'}</td>
                      <td className={sharpeCol.cls} style={sharpeCol.style}>{_fmtMetric(bt.sharpe, 2)}</td>
                      <td className={sortinoCol.cls} style={sortinoCol.style}>{_fmtMetric(bt.sortino, 2)}</td>
                      <td className={calmarCol.cls} style={calmarCol.style}>{_fmtMetric(bt.calmar, 2)}</td>
                      <td className={ddCol.cls} style={ddCol.style}>{bt.max_dd_pct != null ? fmtNum(bt.max_dd_pct, 2) + '%' : '—'}</td>
                      <td className={winCol.cls} style={winCol.style}>{bt.win_rate != null ? fmtNum(bt.win_rate, 1) + '%' : '—'}</td>
                      <td className={pfCol.cls} style={pfCol.style}>{_fmtMetric(bt.profit_factor, 2)}</td>
                      <td className={tstatCol.cls} style={tstatCol.style}>{_fmtMetric(bt.t_stat, 2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Charts row */}
        <div className="bt-charts">
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Equity Curves <span className="count">{checkedIds.size} SELECTED</span></div>
              <div className="panel-actions">
                <div className="seg-ctrl">
                  <button className={equityMode === 'absolute' ? 'active' : ''} onClick={() => setEquityMode('absolute')}>ABS</button>
                  <button className={equityMode === 'normalised' ? 'active' : ''} onClick={() => setEquityMode('normalised')}>NORM</button>
                </div>
              </div>
            </div>
            <div style={{ position: 'relative', height: 280 }}>
              <div ref={equityChartRef} style={{ position: 'absolute', inset: 0 }} />
              {checkedIds.size === 0 && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-3)', letterSpacing: '0.06em' }}>CHECK RUNS TO COMPARE</span>
                </div>
              )}
            </div>
          </div>
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Drawdown <span className="count">{focusedBt ? focusedBt.name || focusedBt.id : 'NONE'}</span></div>
            </div>
            <div style={{ position: 'relative', height: 280 }}>
              <div ref={drawdownChartRef} style={{ position: 'absolute', inset: 0 }} />
              {(!focusedBt || isSummaryOnly) && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
                  <span style={{ fontSize: 12, color: 'var(--text-3)', letterSpacing: '0.06em' }}>{isSummaryOnly ? 'SUMMARY ONLY' : 'SELECT A RUN'}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Analytics row */}
        <div className="bt-analytics">
          {/* Monthly heatmap */}
          <div className="panel bt-heatmap">
            <div className="panel-header">
              <div className="panel-title">Monthly Returns <span className="count">{focusedBt ? focusedBt.name || focusedBt.id : '—'}</span></div>
            </div>
            <div style={{ padding: '10px 12px', overflow: 'auto' }}>
              {!monthly.length ? (
                <div style={{ padding: '16px 0', textAlign: 'center', color: 'var(--text-3)', fontSize: 11 }}>NO DATA</div>
              ) : (
                <div className="hm-grid">
                  <div className="hm-cell hm-header" />
                  {monthLabels.map(m => <div key={m} className="hm-cell hm-header">{m}</div>)}
                  <div className="hm-cell hm-header">YR</div>
                  {heatmapYears.map(({ year, months }) => {
                    const yearPnl = Object.values(months).reduce((s, m) => s + (m.net_pnl || 0), 0);
                    const yearRet = Object.values(months).reduce((s, m) => s + (m.return_pct || 0), 0);
                    return (
                      <React.Fragment key={year}>
                        <div className="hm-cell hm-year-col">{year}</div>
                        {Array.from({ length: 12 }, (_, i) => i + 1).map(mo => {
                          const data = months[mo];
                          const ret = data ? data.return_pct : null;
                          const isPos = ret != null && ret >= 0;
                          const intensity = ret != null ? Math.min(1, Math.abs(ret) / 3) : 0;
                          const bg = ret == null ? 'var(--panel)' : isPos
                            ? `color-mix(in oklab, var(--green-soft) ${intensity * 100}%, var(--panel))`
                            : `color-mix(in oklab, var(--red-soft) ${intensity * 100}%, var(--panel))`;
                          const color = ret == null ? 'var(--text-4)' : isPos ? 'var(--green)' : 'var(--red)';
                          return (
                            <div key={mo} className="hm-cell" style={{ background: bg, color }} title={data ? `${data.net_pnl?.toFixed(0)}` : ''}>
                              {ret != null ? `${ret.toFixed(1)}%` : ''}
                            </div>
                          );
                        })}
                        <div className="hm-cell hm-year-col" style={{ color: yearRet >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {yearRet.toFixed(1)}%
                        </div>
                      </React.Fragment>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* PNL Distribution */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">PNL Distribution <span className="count">{ledger?.rows?.length || 0} TRADES</span></div>
            </div>
            <div style={{ padding: '12px 14px', height: 220, display: 'flex', alignItems: 'flex-end', gap: 2 }}>
              {!pnlDist.length ? (
                <div style={{ width: '100%', textAlign: 'center', color: 'var(--text-3)', fontSize: 11 }}>NO DATA</div>
              ) : (
                pnlDist.map((bin, i) => (
                  <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                    <div style={{ height: `${bin.height * 1.6}px`, width: '100%', background: bin.color, opacity: 0.7, minHeight: 2 }} />
                    <span style={{ fontSize: 9, color: 'var(--text-4)', fontFamily: 'JetBrains Mono, monospace', transform: 'rotate(-45deg)', transformOrigin: 'top left', whiteSpace: 'nowrap' }}>{bin.count}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Exit reason breakdown */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Exit Reasons <span className="count">{ledger?.rows?.length || 0} TRADES</span></div>
            </div>
            <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflow: 'auto' }}>
              {!exitBreakdown.length ? (
                <div style={{ textAlign: 'center', color: 'var(--text-3)', fontSize: 11 }}>NO DATA</div>
              ) : (
                exitBreakdown.map(item => (
                  <div key={item.reason} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
                    <span style={{ width: 90, fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.reason}</span>
                    <div style={{ flex: 1, height: 6, background: 'rgba(255,255,255,0.04)' }}>
                      <div style={{ height: '100%', width: `${item.pct}%`, background: item.pnl >= 0 ? 'var(--green)' : 'var(--red)', opacity: 0.6 }} />
                    </div>
                    <span style={{ width: 28, textAlign: 'right', fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-3)' }}>{item.count}</span>
                    <span style={{ width: 60, textAlign: 'right', fontFamily: 'JetBrains Mono, monospace', color: item.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtINR(item.pnl, { dp: 0 })}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Ledger panel */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Trade Ledger <span className="count">{ledger?.total ?? 0} ROWS</span></div>
            {!isSummaryOnly && (
              <div className="panel-actions" style={{ gap: 8 }}>
                {focusedBt && focusedBt.has_decisions && (
                  <button className="ctrl-btn" onClick={() => setShowDecisions(v => !v)}>
                    {showDecisions ? 'HIDE DECISIONS' : 'DECISIONS'}
                  </button>
                )}
                <select className="ctrl-select" value={ledgerFilterSymbol} onChange={e => { setLedgerFilterSymbol(e.target.value); setLedgerPage(0); }} style={{ width: 100 }}>
                  <option value="">ALL SYM</option>
                  {['NIFTY','FINNIFTY','MIDCPNIFTY','SENSEX','BANKNIFTY'].map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <select className="ctrl-select" value={ledgerFilterReason} onChange={e => { setLedgerFilterReason(e.target.value); setLedgerPage(0); }} style={{ width: 120 }}>
                  <option value="">ALL REASONS</option>
                  {exitReasons.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            )}
          </div>
          {isSummaryOnly ? (
            <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-3)', fontSize: 12, letterSpacing: '0.06em' }}>SUMMARY ONLY — NO TRADE LEDGER SAVED</div>
          ) : (
            <>
              <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
                <table className="tbl" style={{ minWidth: 1000 }}>
                  <thead>
                    <tr>
                      <th className="l">#</th>
                      <th className="l">ENTRY DATE</th>
                      <th className="l">EXIT DATE</th>
                      <th>SYMBOL</th>
                      <th>EXPIRY</th>
                      <th>DTE</th>
                      <th>VIX</th>
                      <th>VIX BUCKET</th>
                      <th>ENTRY CREDIT</th>
                      <th>GROSS PNL</th>
                      <th>NET PNL</th>
                      <th className="l">EXIT REASON</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger && ledger.rows.map((r, i) => {
                      const isPos = (r.net_pnl || 0) >= 0;
                      return (
                        <tr key={i} className={`pos-row ${isPos ? 'pnl-pos' : 'pnl-neg'}`}>
                          <td className="l muted">{ledger.page * ledger.size + i + 1}</td>
                          <td className="l">{r.entry_date || '—'}</td>
                          <td className="l">{r.exit_date || '—'}</td>
                          <td>{r.symbol || '—'}</td>
                          <td>{r.expiry || '—'}</td>
                          <td>{r.dte != null ? r.dte : '—'}</td>
                          <td>{r.vix != null ? fmtNum(r.vix, 2) : '—'}</td>
                          <td>{r.vix_bucket || '—'}</td>
                          <td>{fmtNum(r.entry_credit, 2)}</td>
                          <td className={isPos ? 'pos' : 'neg'}>{fmtINR(r.gross_pnl, { dp: 0 })}</td>
                          <td className={isPos ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>{fmtINR(r.net_pnl, { dp: 0 })}</td>
                          <td className="l">{r.exit_reason || '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="panel-footer">
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="page-btn" onClick={() => setLedgerPage(0)} disabled={!ledger || ledger.page <= 0}>FIRST</button>
                  <button className="page-btn" onClick={() => setLedgerPage(p => Math.max(0, p - 1))} disabled={!ledger || ledger.page <= 0}>PREV</button>
                  <button className="page-btn" onClick={() => setLedgerPage(p => p + 1)} disabled={!ledger || (ledger.page + 1) * ledger.size >= ledger.total}>NEXT</button>
                </div>
                <span style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
                  PAGE {ledger ? ledger.page + 1 : 1} / {ledger ? Math.max(1, Math.ceil(ledger.total / ledger.size)) : 1} · {ledger ? ledger.total : 0} ROWS
                </span>
              </div>
            </>
          )}
        </div>

        {/* Decision drawer */}
        {showDecisions && decisions && (
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Decision Log <span className="count">{decisions.total || 0} ROWS</span></div>
              <div className="panel-actions">
                <button className="ctrl-btn" onClick={() => setShowDecisions(false)}>CLOSE</button>
              </div>
            </div>
            <div style={{ overflowX: 'auto', maxHeight: 320, overflowY: 'auto' }}>
              {!decisions.rows || !decisions.rows.length ? (
                <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-3)', fontSize: 12, letterSpacing: '0.06em' }}>NO DECISIONS</div>
              ) : (
                <table className="tbl" style={{ minWidth: 850 }}>
                  <thead>
                    <tr>
                      <th className="l">TS</th>
                      <th>SYMBOL</th>
                      <th>DECISION</th>
                      <th className="l">REASON</th>
                      <th>VIX</th>
                      <th>DTE</th>
                      <th>EXPIRY</th>
                      <th>ELIGIBLE</th>
                      <th>SELECTED</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisions.rows.map((r, i) => (
                      <tr key={i}>
                        <td className="l muted">{r.ts ? r.ts.slice(0,19).replace('T',' ') : '—'}</td>
                        <td>{r.symbol || '—'}</td>
                        <td style={{ color: r.decision === 'ENTER' ? 'var(--green)' : r.decision === 'SKIP' ? 'var(--amber)' : 'var(--text)' }}>{r.decision || '—'}</td>
                        <td className="l">{r.reason || '—'}</td>
                        <td>{r.vix != null ? fmtNum(r.vix, 2) : '—'}</td>
                        <td>{r.dte != null ? r.dte : '—'}</td>
                        <td>{r.expiry || '—'}</td>
                        <td>{r.eligible != null ? (r.eligible ? 'Y' : 'N') : '—'}</td>
                        <td>{r.selected != null ? (r.selected ? 'Y' : 'N') : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { BacktestsTab });
