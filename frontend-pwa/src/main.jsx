import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

// Register the service worker so the app itself survives losing the network —
// an offline queue is useless if the page cannot load in the first place.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
