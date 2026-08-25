import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

// Register the service worker so the app itself survives losing the network —
// an offline queue is useless if the page cannot load in the first place.
//
// The update path matters as much as the registration. Without it a phone that
// has opened SETU once keeps that build until someone clears site data by hand,
// which is not a thing you can ask of a citizen, or of a judge holding a phone.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js')
      .then((reg) => {
        // A new worker is waiting: tell it to take over now rather than
        // sitting behind every open tab.
        if (reg.waiting) reg.waiting.postMessage({ type: 'skip-waiting' })

        reg.addEventListener('updatefound', () => {
          const incoming = reg.installing
          if (!incoming) return
          incoming.addEventListener('statechange', () => {
            if (incoming.state === 'installed' && navigator.serviceWorker.controller) {
              incoming.postMessage({ type: 'skip-waiting' })
            }
          })
        })

        // Check for a new build on every load. Cheap: one conditional request.
        reg.update().catch(() => {})
      })
      .catch(() => {})

    // When the new worker takes control, reload once so the fresh bundle is
    // actually running. The guard stops the reload loop that this otherwise
    // causes on first install.
    let reloading = false
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloading) return
      reloading = true
      window.location.reload()
    })
  })
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
