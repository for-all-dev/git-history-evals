import { useEffect, useState } from 'react'
import App from './App'
import { Leaderboard } from './leaderboard/Leaderboard'
import './Root.css'

type Route = 'playground' | 'leaderboard'

function routeFromHash(): Route {
  return window.location.hash === '#leaderboard' ? 'leaderboard' : 'playground'
}

export default function Root() {
  const [route, setRoute] = useState<Route>(routeFromHash)

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <>
      <nav className="top-nav">
        <a href="#" className={route === 'playground' ? 'on' : ''}>
          Playground
        </a>
        <a href="#leaderboard" className={route === 'leaderboard' ? 'on' : ''}>
          Leaderboard
        </a>
      </nav>
      {route === 'playground' ? <App /> : <Leaderboard />}
    </>
  )
}
