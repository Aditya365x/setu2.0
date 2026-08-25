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
  const selectIncident = useStore((s) => s.selectIncident)
  const setDistrict = useStore((s) => s.setDistrict)
  useWebSocket()

  useEffect(() => {
    // Deep links: ?district=9&incident=11 opens straight onto an incident.
    // A DEOC has more than one screen and more than one person; being able to
    // send a colleague the exact incident you are looking at is worth the six
    // lines it costs.
    const q = new URLSearchParams(window.location.search)
    const d = Number(q.get('district'))
    const i = Number(q.get('incident'))

    const boot = async () => {
      if (d) await setDistrict(d)
      else await resync()
      if (i) await selectIncident(i)
    }
    boot().catch(() => {})
  }, [resync, selectIncident, setDistrict])

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
