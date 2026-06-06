import { useEffect, useState } from 'react'

type Status = 'checking' | 'ok' | 'degraded' | 'offline'

const API_BASE = window.api?.baseUrl ?? 'http://localhost:8000'

const COLOR: Record<Status, string> = {
  checking: '#6b7280',
  ok:       '#22c55e',
  degraded: '#f59e0b',
  offline:  '#ef4444',
}

const LABEL: Record<Status, string> = {
  checking: 'Checking…',
  ok:       'Backend OK',
  degraded: 'Degraded',
  offline:  'Offline',
}

export default function HealthBadge(): JSX.Element {
  const [status, setStatus] = useState<Status>('checking')

  useEffect(() => {
    const check = async (): Promise<void> => {
      try {
        const res = await fetch(`${API_BASE}/health`, {
          signal: AbortSignal.timeout(4000),
        })
        if (res.ok) {
          const data = (await res.json()) as { status: string }
          setStatus(data.status === 'ok' ? 'ok' : 'degraded')
        } else {
          setStatus('offline')
        }
      } catch {
        setStatus('offline')
      }
    }

    check()
    const id = setInterval(check, 10_000)
    return () => clearInterval(id)
  }, [])

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontSize: '0.8rem', fontWeight: 500, color: COLOR[status],
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: COLOR[status], display: 'inline-block',
        boxShadow: status === 'ok' ? `0 0 6px ${COLOR.ok}` : 'none',
      }} />
      {LABEL[status]}
    </span>
  )
}
