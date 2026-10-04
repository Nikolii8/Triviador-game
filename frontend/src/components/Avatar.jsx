const AVATAR_COLORS = {
  'knight-1': '#c0392b',
  'knight-2': '#2471a3',
  'knight-3': '#1e8449',
  'knight-4': '#b7950b',
}

export function Avatar({ avatarKey, size = 64 }) {
  const color = AVATAR_COLORS[avatarKey] || '#7f8c8d'
  const number = avatarKey?.split('-')[1] ?? '?'
  return (
    <svg className="avatar" width={size} height={size} viewBox="0 0 64 64" role="img" aria-label={avatarKey}>
      <path d="M32 4 L56 12 V30 C56 46 45 56 32 60 C19 56 8 46 8 30 V12 Z" fill={color} />
      <path d="M32 10 L50 16 V30 C50 42 42 50 32 54 C22 50 14 42 14 30 V16 Z" fill="none" stroke="#fff" strokeOpacity="0.5" strokeWidth="2" />
      <text x="32" y="40" textAnchor="middle" fontSize="22" fontWeight="700" fill="#fff">
        {number}
      </text>
    </svg>
  )
}
