import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import App from './App'
import { mockServer, signIn } from './test/server'
import { renderWithProviders } from './test/render'

/**
 * The app shell: signing in, signing out, and a session expiring.
 *
 * The e2e suite injects a token before the page loads, so it never exercised
 * the transition from the login form into the app - which is where App.tsx
 * called `useAc` after an early `return <Login/>`, a hook-order violation that
 * blanked the screen on every form login, every sign-out and every 401.
 */
describe('App', () => {
  it('signs in through the form and shows the dashboard', async () => {
    const server = mockServer({
      'POST /auth/login': {
        body: { access_token: 'tok', role: 'admin', name: 'Test admin', block_id: null },
      },
    })
    signIn(server)
    localStorage.removeItem('giridih.token')   // start signed out
    renderWithProviders(<App />)

    await userEvent.type(await screen.findByLabelText('User ID'), '9000000001')
    await userEvent.type(screen.getByLabelText('Password'), 'pw-123456')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(localStorage.getItem('giridih.token')).toBe('tok')
  })

  it('signs out back to the login form and forgets cached data', async () => {
    const server = mockServer()
    signIn(server)
    const { client } = renderWithProviders(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByLabelText('User ID')).toBeInTheDocument()
    expect(localStorage.getItem('giridih.token')).toBeNull()
    // The next user must not be shown this user's role or data.
    expect(client.getQueryData(['me'])).toBeUndefined()
  })

  it('returns to the login form when a request comes back 401', async () => {
    const server = mockServer()
    signIn(server)
    server.on('/acs/32/summary', { status: 401, body: { detail: 'expired' } })
    renderWithProviders(<App />)

    expect(await screen.findByLabelText('User ID')).toBeInTheDocument()
    expect(screen.getByText(/session expired/i)).toBeInTheDocument()
  })

  it('says the password is wrong instead of "session expired" on a failed login', async () => {
    mockServer({
      'POST /auth/login': { status: 401, body: { detail: 'Incorrect user id or password' } },
    })
    renderWithProviders(<App />)

    await userEvent.type(await screen.findByLabelText('User ID'), '9000000001')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-pw')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/incorrect user id or password/i)).toBeInTheDocument()
    expect(screen.queryByText(/session expired/i)).not.toBeInTheDocument()
  })

  it('shows a readable message when the API rejects the input (422)', async () => {
    mockServer({
      'POST /auth/login': {
        status: 422,
        body: { detail: [{ loc: ['body', 'password'], msg: 'String should have at least 6 characters' }] },
      },
    })
    renderWithProviders(<App />)

    await userEvent.type(await screen.findByLabelText('User ID'), '9000000001')
    await userEvent.type(screen.getByLabelText('Password'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/at least 6 characters/)).toBeInTheDocument()
    expect(screen.queryByText(/object Object/)).not.toBeInTheDocument()
  })

  it('shows an error, not an endless spinner, when /config cannot be loaded', async () => {
    const server = mockServer({ '/config': { status: 500, body: { detail: 'boom' } } })
    signIn(server)
    renderWithProviders(<App />)

    await waitFor(() =>
      expect(screen.getByText(/could not load the list of constituencies/i)).toBeInTheDocument())
  })
})
