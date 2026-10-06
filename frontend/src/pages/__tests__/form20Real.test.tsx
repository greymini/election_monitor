import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'

import BoothDrawer from '../../components/BoothDrawer'
import CandidateTable from '../../components/CandidateTable'
import PartyChip from '../../components/PartyChip'
import { boothLeaderUpset, candidateLabel, type ElectionRow } from '../../lib/results'
import Overview from '../Overview'
import liveCandidates from '../../test/data/live-candidates-2024.json'
import liveCard from '../../test/data/live-real-booth-card.json'
import liveSummary from '../../test/data/live-summary.json'
import { mockServer } from '../../test/server'
import { fakeAc, renderWithProviders } from '../../test/render'

/**
 * The pages against the real Form 20 load. The three JSON files are verbatim
 * responses from `scripts/dev_stack.py` (real mode): GET /acs/32/summary,
 * /elections/VS-2024/candidates and /booths/32-B0001/card.
 */

const e2024 = (liveSummary.elections as ElectionRow[]).find((e) => e.label === 'VS-2024')!

describe('Overview on the real Form 20', () => {
  it('states the declared result by name, with the postal split', async () => {
    mockServer({ '/acs/32/summary': { body: liveSummary } })
    renderWithProviders(<Overview ac={fakeAc(32)} />)
    const headline = await screen.findByTestId('result-headline')
    expect(headline).toHaveTextContent('Sudivya Kumar (JMM) won with 94,042 votes')
    expect(headline).toHaveTextContent("Nirbhay Kumar Shahabadi (BJP)'s 90,204")
    expect(headline).toHaveTextContent('3,838 (1.85% of valid votes)')
    expect(headline).toHaveTextContent('2,05,777')     // EVM
    expect(headline).toHaveTextContent('1,905')        // postal
    expect(within(headline).getByTestId('booth-upset'))
      .toHaveTextContent('Nirbhay Kumar Shahabadi led 193 polling stations to Sudivya Kumar\'s 166')
  })

  it('shows the 2014 result as published, with its source', async () => {
    mockServer({ '/acs/32/summary': { body: liveSummary } })
    renderWithProviders(<Overview ac={fakeAc(32)} />)
    await screen.findByTestId('result-headline')
    const row = screen.getByText('VS-2014').closest('tr') as HTMLElement
    expect(row).toHaveTextContent('Nirbhay Kumar Shahabadi')
    expect(row).toHaveTextContent('published')
    expect(row).toHaveTextContent('9,933')
  })

  it('has no synthetic banner on the real load', async () => {
    mockServer({ '/acs/32/summary': { body: liveSummary } })
    renderWithProviders(<Overview ac={fakeAc(32)} />)
    await screen.findByTestId('result-headline')
    expect(screen.queryByTestId('synthetic-banner')).not.toBeInTheDocument()
  })
})

describe('CandidateTable', () => {
  it('lists all fourteen candidates and NOTA, ranked, with deposits', async () => {
    mockServer({ '/acs/32/elections/VS-2024/candidates': { body: liveCandidates } })
    renderWithProviders(<CandidateTable ac={fakeAc(32)} election="VS-2024" />)
    const table = await screen.findByTestId('candidate-table')
    const rows = within(table).getAllByRole('row')
    // header + 14 candidates + NOTA + total
    expect(rows).toHaveLength(17)
    expect(rows[1]).toHaveTextContent('Sudivya Kumar')
    expect(rows[1]).toHaveTextContent('elected')
    expect(rows[1]).toHaveTextContent('94,042')
    expect(rows[3]).toHaveTextContent('Navin Anand')
    expect(rows[3]).toHaveTextContent('deposit forfeited')
    expect(within(table).getAllByText('party not in source')).toHaveLength(11)
    expect(rows[16]).toHaveTextContent('2,07,682')
  })
})

describe('BoothDrawer on a real booth', () => {
  it('names every candidate and the change since 2019', async () => {
    mockServer({ '/acs/32/booths/32-B0001/card': { body: liveCard } })
    renderWithProviders(<BoothDrawer boothUid="32-B0001" ac={fakeAc(32)} onClose={() => {}} />)
    const tables = await screen.findAllByTestId('booth-candidates')
    expect(tables).toHaveLength(2)
    expect(within(tables[0]).getAllByRole('row')).toHaveLength(15)
    expect(within(tables[1]).getAllByRole('row')).toHaveLength(13)
    const sudivya = within(tables[0]).getByText('Sudivya Kumar').closest('tr') as HTMLElement
    expect(sudivya.textContent).toMatch(/[+-]?\d+\.\d pt/)
  })
})

describe('result helpers', () => {
  it('never shows UNK as a party', () => {
    expect(candidateLabel('Arundhati Mishra', 'UNK')).toBe('Arundhati Mishra')
    expect(candidateLabel('Sudivya Kumar', 'JMM')).toBe('Sudivya Kumar (JMM)')
    renderWithProviders(<PartyChip abbr="UNK:Arundhati Mishra" />)
    expect(screen.getByText('party not in source')).toBeInTheDocument()
  })

  it('finds the booth leader who lost', () => {
    const upset = boothLeaderUpset(e2024)
    expect(upset?.leader.candidate).toBe('Nirbhay Kumar Shahabadi')
    expect(upset?.winner?.booths).toBe(166)
    expect(boothLeaderUpset({ ...e2024, booths_led: [] })).toBeNull()
  })
})
