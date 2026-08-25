import { useEffect, useRef } from 'react'
import { useStore } from '../store'
import { wsUrl } from '../api'

const MIN_BACKOFF = 1000
const MAX_BACKOFF = 30000

/**
 * Live link to the DEOC event stream.
 *
 * Exponential backoff plus a full resync on every reconnect. Build this on day
 * one: the demo laptop WILL sleep at some point, and a map that silently stops
 * updating is worse than one that visibly reconnects.
 */
export function useWebSocket() {
  const backoff = useRef(MIN_BACKOFF)
  const socket = useRef(null)
  const closed = useRef(false)
  // Re-subscribe when the operator switches district, or the map keeps
  // receiving another district's events.
  const districtId = useStore((s) => s.districtId)

  useEffect(() => {
    closed.current = false
    const { applyEvent, resync, setConnection } = useStore.getState()
    let timer = null

    const connect = () => {
      if (closed.current) return
      setConnection('connecting')

      const ws = new WebSocket(wsUrl(`/api/v1/ws?district_id=${districtId}`))
      socket.current = ws

      ws.onopen = () => {
        backoff.current = MIN_BACKOFF
        setConnection('live')
        // Resync before trusting a single incremental event.
        resync().catch(() => {})
        ws.send(JSON.stringify({ type: 'subscribe' }))
      }

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          if (parsed.type === 'heartbeat') return
          applyEvent(parsed)
        } catch {
          /* a malformed frame must never take the dashboard down */
        }
      }

      ws.onclose = () => {
        if (closed.current) return
        setConnection('reconnecting')
        timer = setTimeout(connect, backoff.current)
        backoff.current = Math.min(backoff.current * 2, MAX_BACKOFF)
      }

      ws.onerror = () => ws.close()
    }

    connect()
    const keepalive = setInterval(() => {
      if (socket.current?.readyState === WebSocket.OPEN) {
        socket.current.send(JSON.stringify({ type: 'heartbeat' }))
      }
    }, 15000)

    return () => {
      closed.current = true
      clearInterval(keepalive)
      if (timer) clearTimeout(timer)
      socket.current?.close()
    }
  }, [districtId])
}
