import type { CSSProperties } from 'react'
import '../skeleton.css'

// Placeholder shapes shown while the first result for a view is loading. They are decorative: the
// role="status" text next to them stays the accessible loading message.

export function Skeleton({ width, height, radius, className = '' }: { width?: string | number; height?: string | number; radius?: string | number; className?: string }) {
  const style: CSSProperties = { width, height, borderRadius: radius }
  return <span className={`skeleton ${className}`} style={style} />
}

function ChartSkeleton({ bars = 24, label }: { bars?: number; label: string }) {
  return <div className="skeleton-panel skeleton-chart-panel">
    <Skeleton width={label} height={14} />
    <Skeleton width="70%" height={10} />
    <div className="skeleton-chart">
      {Array.from({ length: bars }, (_, index) => (
        <span key={index} className="skeleton skeleton-bar" style={{ height: `${30 + Math.round(45 * Math.abs(Math.sin(index / 3.2)))}%` }} />
      ))}
    </div>
  </div>
}

export function PlanSkeleton() {
  return <div className="skeleton-group" aria-hidden="true" data-testid="plan-skeleton">
    <div className="skeleton-row">
      <Skeleton width={72} height={24} radius={999} />
      <Skeleton width={56} height={24} radius={999} />
    </div>
    <div className="skeleton-cards">
      {['Cost', 'CO2', 'Generator'].map((key) => <div key={key} className="skeleton-panel skeleton-card">
        <Skeleton width="55%" height={11} />
        <Skeleton width="75%" height={28} />
      </div>)}
    </div>
    <div className="skeleton-panel">
      <Skeleton width="40%" height={14} />
      <Skeleton width="90%" height={10} />
      <Skeleton width="80%" height={10} />
    </div>
    <ChartSkeleton label="35%" />
    <ChartSkeleton label="28%" bars={0} />
  </div>
}

export function TradeoffSkeleton() {
  return <div className="skeleton-group" aria-hidden="true" data-testid="tradeoff-skeleton">
    <div className="skeleton-panel skeleton-scatter">
      <Skeleton width="30%" height={14} />
      <div className="skeleton-plot">
        {[[12, 20], [26, 34], [40, 46], [55, 58], [70, 66], [84, 72]].map(([x, y]) => (
          <span key={x} className="skeleton skeleton-dot" style={{ left: `${x}%`, bottom: `${y}%` }} />
        ))}
      </div>
    </div>
    <div className="skeleton-cards">
      {[0, 1].map((key) => <div key={key} className="skeleton-panel skeleton-card">
        <Skeleton width="50%" height={11} />
        <Skeleton width="70%" height={24} />
      </div>)}
    </div>
  </div>
}

export function SelectSkeleton() {
  return <Skeleton className="skeleton-select" width="100%" height={40} radius={10} />
}
