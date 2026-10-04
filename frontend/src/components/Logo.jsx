export function Logo({ small = false }) {
  return (
    <div className={`logo${small ? ' logo--small' : ''}`}>
      {!small && <span className="logo__welcome">Welcome to</span>}
      <span className="logo__title">Triviador</span>
    </div>
  )
}
