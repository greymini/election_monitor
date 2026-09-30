import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import Layout from './components/Layout'
import { Loading } from './components/States'
import Login from './pages/Login'
import Overview from './pages/Overview'
const MapExplorer = lazy(() => import('./pages/MapExplorer'))
const Results = lazy(() => import('./pages/Results'))
const Voters = lazy(() => import('./pages/Voters'))
const Caste = lazy(() => import('./pages/Caste'))
const Transfer = lazy(() => import('./pages/Transfer'))
const LocalPolls = lazy(() => import('./pages/LocalPolls'))
const News = lazy(() => import('./pages/News'))
const Factors = lazy(() => import('./pages/Factors'))
const Scenario = lazy(() => import('./pages/Scenario'))
const Admin = lazy(() => import('./pages/Admin'))
import { getConfig, getMe, getToken, setSession, type AppConfig, type Me } from './lib/api'

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => Boolean(getToken()))

  // The API client fires this when any request comes back 401.
  useEffect(() => {
    const onUnauthorised = () => setAuthed(false)
    window.addEventListener('giridih:unauthorised', onUnauthorised)
    return () => window.removeEventListener('giridih:unauthorised', onUnauthorised)
  }, [])

  const me = useQuery<Me>({ queryKey: ['me'], queryFn: getMe, enabled: authed })

  /** Feature flags, fetched once and cached for the session. `staleTime: Infinity`
   *  because these only change when the server restarts. */
  const config = useQuery<AppConfig>({
    queryKey: ['config'],
    queryFn: getConfig,
    staleTime: Infinity,
    retry: false,
  })

  if (!authed) {
    return <Login onSignedIn={() => setAuthed(true)} />
  }

  const signOut = () => {
    setSession(null)
    setAuthed(false)
  }

  return (
    <Layout me={me.data} config={config.data} onSignOut={signOut}>
      <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/map" element={<MapExplorer />} />
        <Route path="/results" element={<Results />} />
        <Route path="/results/:electionLabel" element={<Results />} />
        <Route path="/voters" element={<Voters />} />
        {me.data?.sees_caste && <Route path="/caste" element={<Caste />} />}
        <Route path="/transfer" element={<Transfer />} />
        <Route path="/local" element={<LocalPolls />} />
        <Route path="/news" element={<News />} />
        <Route path="/factors" element={<Factors />} />
        <Route path="/scenario" element={<Scenario />} />
        {me.data?.role === 'admin' && <Route path="/admin" element={<Admin />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>
    </Layout>
  )
}
