interface Props {
  checked: boolean
  onChange: (checked: boolean) => void
  // Accessible name for the switch (no visible text label).
  label: string
}

/** A small on/off pill switch, in the style of the dark/light-mode toggles. */
export default function Toggle({ checked, onChange, label }: Props): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`toggle-switch${checked ? ' on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-text">{checked ? 'ON' : 'OFF'}</span>
      <span className="toggle-knob" />
    </button>
  )
}
