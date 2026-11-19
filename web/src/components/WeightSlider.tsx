import { useId } from 'react'

export default function WeightSlider({ value = 1, onChange }: {
  value?: number
  onChange: (value: number) => void
}) {
  const id = useId()
  const meaning = value === 0 ? 'Cleanest' : value === 1 ? 'Cheapest'
    : value < 0.5 ? 'weighted toward lower CO2' : value > 0.5 ? 'weighted toward lower cost'
      : 'equal cost and CO2 weighting'
  const valueText = `w = ${value.toFixed(2)} - ${meaning}`

  return <div className="weight-control">
    <label htmlFor={id}>Cost vs carbon</label>
    <input id={id} type="range" min={0} max={1} step={0.05} value={value}
      aria-valuetext={valueText} aria-describedby={`${id}-note`}
      onChange={(event) => onChange(event.target.valueAsNumber)} />
    <div className="weight-endpoints"><span>Cleanest</span><span>Cheapest</span></div>
    <p className="weight-value">{valueText}</p>
    <p id={`${id}-note`} className="muted weight-note">Cost and CO2 trade off only a little on this site because the grid CO2 factor is flat. The plan changes mostly between Cleanest and about w = 0.26; from there up it is the same cheapest plan.</p>
  </div>
}
