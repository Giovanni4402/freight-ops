/**
 * Works out where a shipment is, from what the mailboxes say.
 *
 * We're a freight forwarder. A parser reads our shared mailboxes and pulls
 * out travel events; accounting data comes from the ERP side. The two
 * disagree all the time and this decides who wins.
 *
 * Order matters. First match wins.
 */

const LOADED = ['loaded', 'departed', 'on_board', 'berthed', 'cleared', 'delayed'] as const
const ARRIVED = ['delivered', 'collected'] as const

// 15 not 7: a week means every August shipment goes "unclear".
const SILENCE_DAYS = 15

export type State =
  | 'delivered' | 'settled' | 'archived' | 'unclear'
  | 'in_transit' | 'loading_overdue' | 'awaiting_loading'

export interface Shipment {
  jobRef: string | null
  jobStatus: 'open' | 'closed'
  travelState: string | null
  proofDate: Date | null
  proofKinds: string | null       // e.g. "pod,cmr"
  lastMessage: Date | null
  pickupDate: Date | null
  goodsReadyDate: Date | null
  departureDate: Date | null
  revenueToInvoice: number
  costToReceive: number
  fullySettled: boolean
  lineCount: number
}

// reason is rendered in the UI next to the status. Also how I debug this.
export interface Verdict { state: State; reason: string }

const days = (a: Date, b: Date) => Math.floor((a.getTime() - b.getTime()) / 86_400_000)

export function shipmentState(s: Shipment, today: Date): Verdict {
  if (s.proofDate)
    return { state: 'delivered', reason: `arrival document (${s.proofKinds}) dated ${fmt(s.proofDate)}` }

  if (s.travelState && ARRIVED.includes(s.travelState as never))
    return { state: 'delivered', reason: 'correspondence says delivered' }

  // Accounting beats the parser. Had a delivered job jump back to in_transit
  // because someone chased an unpaid invoice and the parser read the thread
  // as fresh shipping activity. Closed + settled, we stop listening.
  const nothingOutstanding = !s.revenueToInvoice && !s.costToReceive
  if (s.jobStatus === 'closed' && nothingOutstanding)
    return { state: 'settled', reason: 'job closed, invoiced and paid' }

  const silent = s.lastMessage ? days(today, s.lastMessage) : null
  if (silent !== null && silent > SILENCE_DAYS) {
    // No job ref and nobody's written in weeks: it was never a real job.
    if (!s.jobRef)
      return { state: 'archived', reason: `no job ref, nothing heard for ${silent} days` }
    const last = s.travelState ? `, last we heard: ${s.travelState}` : ''
    return { state: 'unclear', reason: `job open, nothing heard for ${silent} days${last}` }
  }

  // Same check again, for jobs still open on paper. Money doesn't move both
  // ways on goods sitting in a yard.
  if (s.fullySettled && nothingOutstanding)
    return { state: 'settled', reason: 'collected and paid' }

  if (s.travelState && LOADED.includes(s.travelState as never))
    return { state: 'in_transit', reason: `correspondence says ${s.travelState}` }

  const scheduled = s.pickupDate ?? s.goodsReadyDate ?? s.departureDate
  if (scheduled && scheduled < today)
    return { state: 'loading_overdue', reason: `loading due ${fmt(scheduled)}, no departure recorded` }
  if (scheduled)
    return { state: 'awaiting_loading', reason: `loading due ${fmt(scheduled)}` }

  // Used to report these as "awaiting loading" too. Ops hated it: nothing was
  // scheduled, the job had just been opened and left empty. Own up to it.
  if (!s.lineCount)
    return { state: 'awaiting_loading', reason: 'job just opened, nothing filled in yet' }

  return { state: 'awaiting_loading', reason: 'no loading date yet' }
}

const fmt = (d: Date) => d.toISOString().slice(0, 10)
