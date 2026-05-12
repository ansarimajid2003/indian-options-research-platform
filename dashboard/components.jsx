// Shared UI primitives (icons, badges, sparkline)

const { useEffect, useRef, useState, useMemo } = React;

const Icon = React.memo(function Icon({ name, size = 12, color = 'currentColor' }) {
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
});

const MarketBadge = React.memo(function MarketBadge({ status }) {
  const cls = status === 'OPEN' ? 'open' : status === 'PRE_OPEN' ? 'preopen' : status === 'HOLIDAY' ? 'holiday' : 'closed';
  return <span className={`badge ${cls}`}><span className="dot" />{status}</span>;
});

const Sparkline = React.memo(function Sparkline({ data, width = 110, height = 22, color = 'oklch(0.78 0.18 150)' }) {
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
});

const Meter = React.memo(function Meter({ value, max = 100, warnAt = 85, critAt = 70 }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const cls = value < critAt ? 'crit' : value < warnAt ? 'warn' : '';
  return <div className="meter"><div className={`meter-fill ${cls}`} style={{ width: `${pct}%` }} /></div>;
});

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
