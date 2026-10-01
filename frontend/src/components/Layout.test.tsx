import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import Layout from './Layout'
import { fakeAc, renderWithProviders } from '../test/render'

const me = { user_id: 1, name: 'Block user', role: 'block' as const, block_id: 3202,
  sees_caste: false, daily_token_budget: 1 }

describe('Layout', () => {
  it('offers sign-out inside the mobile menu', async () => {
    const onSignOut = vi.fn()
    renderWithProviders(
      <Layout me={me} config={undefined} ac={fakeAc(32)} onSignOut={onSignOut}>x</Layout>)
    await userEvent.click(screen.getByRole('button', { name: 'Menu' }))
    const sheets = screen.getAllByRole('navigation')
    const mobile = sheets[sheets.length - 1]
    await userEvent.click(within(mobile).getByRole('button', { name: 'Sign out' }))
    expect(onSignOut).toHaveBeenCalled()
  })

  it('still offers sign-out when the user could not be loaded', () => {
    renderWithProviders(
      <Layout me={undefined} config={undefined} ac={fakeAc(32)} onSignOut={() => {}}>x</Layout>)
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('hides the caste and admin menus from a block user', () => {
    renderWithProviders(
      <Layout me={me} config={undefined} ac={fakeAc(32)} onSignOut={() => {}}>x</Layout>)
    expect(screen.queryByRole('button', { name: /admin/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /caste|community/i })).not.toBeInTheDocument()
  })
})
