import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SourceLink } from './Provenance'

afterEach(() => vi.unstubAllEnvs())

describe('SourceLink', () => {
  it('does not link to a path nothing serves', () => {
    vi.stubEnv('VITE_SOURCE_DOCS_URL', '')
    render(<SourceLink doc="form20-vs2024-ac32.pdf" page={7} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(/form20-vs2024-ac32\.pdf p7/)).toBeInTheDocument()
  })

  it('links to the page when a document location is configured', () => {
    vi.stubEnv('VITE_SOURCE_DOCS_URL', 'https://docs.example.org/form20/')
    render(<SourceLink doc="form20-vs2024-ac32.pdf" page={7} />)
    expect(screen.getByRole('link').getAttribute('href'))
      .toBe('https://docs.example.org/form20/form20-vs2024-ac32.pdf#page=7')
  })

  it('shows a dash, never "p?", when the page is unknown', () => {
    render(<SourceLink doc="form20-vs2024-ac32.pdf" page={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
