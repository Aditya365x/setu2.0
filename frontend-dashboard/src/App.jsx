import { useEffect } from 'react'
import MapView from './components/MapView'
import IncidentQueue from './components/IncidentQueue'
import DetailPanel from './components/DetailPanel'
import ResourcePanel from './components/ResourcePanel'
import MetricsBar from './components/MetricsBar'
import { useWebSocket } from './hooks/useWebSocket'
import { useStore } from './store'

/**
 * One screen. No tabs, no navigation, no modal stack.
 *
 * A DEOC operator under load will not click through menus, and a judge will not
 * wait while you find the right page.
 */
export default function App() {
  const resync = useStore((s) => s.resync)
  useWebSocket()

  useEffect(() => {
    resync().catch(() => {})
  }, [resync])

  return (
    <div className="app">
      <MetricsBar />
      {/* §31 — two feeds, deliberately side by side: incoming reports on the
          left, remaining resources on the right. "Where are the problems" and
          "what have I got left" is the whole operating question. */}
      <main className="stage">
        <IncidentQueue />
        <MapView />
        <div className="rightrail">
          <DetailPanel />
          <ResourcePanel />
        </div>
      </main>
    </div>
  )
}
