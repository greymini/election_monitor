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
import { useAc } from './lib/ac'
import { getConfig, getMe, getToken, setSession, type AppConfig, type Me } from './lib/api'
const Compare = lazy(() => import('./pages/Compare'))
const Booths = lazy(() => import('./pages/Booths'))
const CasteScatter = lazy(() => import('./pages/CasteScatter'))
const Candidates = lazy(() => import('./pages/Candidates'))
const LocalPolitics = lazy(() => import('./pages/LocalPolitics'))

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

  /** Selected constituency: ?ac= in the URL, then localStorage, then the first
   *  AC /config lists. Every page takes it so nothing can query unscoped. */
  const ac = useAc(config.data)

  const signOut = () => {
    setSession(null)
    setAuthed(false)
  }

  return (
    <Layout me={me.data} config={config.data} ac={ac} onSignOut={signOut}>
      <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/" element={<Overview ac={ac} />} />
        <Route path="/map" element={<MapExplorer ac={ac} />} />
        <Route path="/booths" element={<Booths ac={ac} />} />
        <Route path="/results" element={<Results ac={ac} />} />
        <Route path="/results/:electionLabel" element={<Results ac={ac} />} />
        <Route path="/voters" element={<Voters ac={ac} />} />
        {me.data?.sees_caste && <Route path="/caste" element={<Caste ac={ac} />} />}
        <Route path="/transfer" element={<Transfer ac={ac} />} />
        <Route path="/local" element={<LocalPolls ac={ac} />} />
        <Route path="/news" element={<News ac={ac} />} />
        <Route path="/factors" element={<Factors ac={ac} />} />
        <Route path="/scenario" element={<Scenario ac={ac} />} />
        <Route path="/compare" element={<Compare />} />
        <Route path="/candidates" element={<Candidates ac={ac} />} />
        <Route path="/local-politics" element={<LocalPolitics ac={ac} />} />
        {me.data?.sees_caste && (
          <Route path="/caste-scatter" element={<CasteScatter ac={ac} />} />
        )}
        {me.data?.role === 'admin' && <Route path="/admin" element={<Admin ac={ac} />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>
    </Layout>
  )
}
