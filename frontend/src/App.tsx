import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import { ErrorState, Loading } from './components/States'
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
import { useTranslation } from 'react-i18next'
const Compare = lazy(() => import('./pages/Compare'))
const Booths = lazy(() => import('./pages/Booths'))
const CasteScatter = lazy(() => import('./pages/CasteScatter'))
const Candidates = lazy(() => import('./pages/Candidates'))
const LocalPolitics = lazy(() => import('./pages/LocalPolitics'))

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => Boolean(getToken()))
  // Set when the API ended the session (401), so the login form can say why.
  const [expired, setExpired] = useState(false)
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const location = useLocation()

  // The API client fires this when any request comes back 401.
  useEffect(() => {
    const onUnauthorised = () => {
      setExpired(true)
      setAuthed(false)
    }
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

  /** Selected constituency: ?ac= in the URL, then localStorage, then the first
   *  AC /config lists. Every page takes it so nothing can query unscoped.
   *
   *  Called before the signed-out early return, never after it: hooks must run
   *  in the same order on every render. It used to sit below `return <Login/>`,
   *  so signing in through the form, signing out, or any 401 changed the hook
   *  count between renders and React unmounted the whole tree - a blank page. */
  const ac = useAc(config.data)

  // Whatever ends the session - sign-out or a 401 - the next person to sign in
  // on this browser must not be served this one's cached role or data.
  useEffect(() => {
    if (!authed) queryClient.clear()
  }, [authed, queryClient])

  if (!authed) {
    return (
      <Login
        notice={expired ? t('login.sessionExpired') : null}
        onSignedIn={() => {
          setExpired(false)
          setAuthed(true)
        }}
      />
    )
  }

  const signOut = () => {
    setSession(null)
    setAuthed(false)
  }

  // /config is fetched once with retry off; on failure there is no AC list and
  // every page would wait on it forever. Say so instead.
  if (config.isError) {
    return (
      <Layout me={me.data} config={undefined} ac={ac} onSignOut={signOut}>
        <ErrorState error={new Error(t('common.configFailed'))} onRetry={() => void config.refetch()} />
      </Layout>
    )
  }

  // N14. The role-gated routes below are registered conditionally, and the
  // catch-all redirects anything unmatched to "/". So a direct navigation to
  // /admin - typing it, a bookmark, a hard refresh - arrived before `me`
  // resolved, matched nothing, and bounced to the Overview. It looked like the
  // page did not exist. Holding the route table until the role is known is
  // enough; the query is already in flight and takes one round trip.
  // Admin and strategist only, matching the API's role checks.
  const analyst = me.data?.role === 'admin' || me.data?.role === 'strategist'

  if (me.isLoading) {
    return (
      <Layout me={undefined} config={config.data} ac={ac} onSignOut={signOut}>
        <Loading />
      </Layout>
    )
  }

  return (
    <Layout me={me.data} config={config.data} ac={ac} onSignOut={signOut}>
      <ErrorBoundary resetKey={`${location.pathname}|${ac.acNumber}`}>
      <Suspense fallback={<Loading />}>
      {/* Keyed by AC: switching constituency remounts the page, so filters
          picked for one AC (a block, an area, an election) do not carry over
          and select nothing in the next. */}
      <Routes key={ac.acNumber ?? 'none'}>
        <Route
          path="/"
          element={<Overview ac={ac} isAdmin={me.data?.role === 'admin'}
                            seesCaste={Boolean(me.data?.sees_caste)} />}
        />
        <Route path="/map" element={<MapExplorer ac={ac} />} />
        <Route path="/booths" element={<Booths ac={ac} />} />
        <Route path="/results" element={<Results ac={ac} />} />
        <Route path="/results/:electionLabel" element={<Results ac={ac} />} />
        <Route path="/voters" element={<Voters ac={ac} />} />
        {me.data?.sees_caste && <Route path="/caste" element={<Caste ac={ac} />} />}
        {analyst && <Route path="/transfer" element={<Transfer ac={ac} />} />}
        <Route path="/local" element={<LocalPolls ac={ac} />} />
        <Route path="/news" element={<News ac={ac} />} />
        <Route path="/factors" element={<Factors ac={ac} />} />
        {analyst && <Route path="/scenario" element={<Scenario ac={ac} />} />}
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
      </ErrorBoundary>
    </Layout>
  )
}
