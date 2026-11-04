import { useEffect, useState, type FormEvent } from 'react'
import { Activity, ArrowRight, Building2, CheckCircle2, Clock3, Database, FileStack, Fingerprint, Link2, LockKeyhole, LogOut, RefreshCw, Search, ShieldCheck, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Input } from '@/components/ui/input'
import { Pagination, PaginationContent, PaginationItem } from '@/components/ui/pagination'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'

type RecordInfo = { id: string; site_id: string; local_patient_id: string; status: string; latest_received_at: string; content_changed_at: string }
type Evidence = { candidate_patient_id: string; candidate_record_id: string; score: number; scoring_version: string; evidence: { matched_tokens: number[]; group_scores: number[]; classification: string } }
type Row = { id: string; site_id?: string; received_at?: string; latest_received_at?: string; status?: string; row_count?: number; duplicate_count?: number; error?: string; hospital_count?: number; reason?: string; created_at?: string; record?: RecordInfo }
type Result = { local_patient_id: string; global_patient_id: string | null; status: string; latest_received_at: string }
type Page<T> = { items: T[]; total: number; offset: number; limit: number }
type Detail = Row & { members?: RecordInfo[]; assessments?: Evidence[]; items?: Result[]; total?: number }
type Version = { id: string; received_at: string; token_version: string; key_id: string; token_count: number; active_for_matching: boolean; assessments: Evidence[] }
type Overview = { patients: number; linked_records: number; hospital_records: number; submissions: number; pending_reviews: number; queued_submissions: number }
const date = (value?: string) => value ? new Date(value).toLocaleString() : '—'
const human = (value?: string) => value?.replaceAll('_', ' ') || '—'

async function request<T>(path: string, token: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1/admin/${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal })
  if (!response.ok) {
    if (response.status === 401) throw new Error('Your administrator credential was not accepted. Sign out and enter a valid credential.')
    throw new Error(`Unable to load this view (${response.status}). Please retry.`)
  }
  return response.json()
}

function useApi<T>(path: string | null, token: string, interval = 15000) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    setData(null); setLoading(true); setError('')
    if (!path) { setLoading(false); return }
    let active = true, controller: AbortController, timer: ReturnType<typeof setTimeout>
    const load = async () => {
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 15000)
      try { const next = await request<T>(path, token, controller.signal); if (active) { setData(next); setError('') } }
      catch (e) { if (active) setError(e instanceof Error && e.name !== 'AbortError' ? e.message : 'Connection interrupted. Retrying automatically; your saved records are retained.') }
      finally { clearTimeout(timeout); if (active) { setLoading(false); timer = setTimeout(load, interval) } }
    }
    load()
    return () => { active = false; clearTimeout(timer); controller?.abort() }
  }, [path, token, interval, refresh])
  return { data, error, loading, reload: () => setRefresh(v => v + 1) }
}

function Status({ value }: { value?: string }) {
  const pending = value === 'pending_review' || value === 'queued' || value === 'open'
  return <Badge variant={value === 'failed' || value === 'rejected' ? 'destructive' : pending ? 'outline' : 'secondary'} className="capitalize whitespace-nowrap">{human(value)}</Badge>
}

function Pager({ offset, total, onChange }: { offset: number; total: number; onChange: (n: number) => void }) {
  return <div className="flex flex-wrap items-center justify-between gap-3 border-t px-6 py-4"><p className="text-xs text-muted-foreground">{total ? `${offset + 1}–${Math.min(offset + 20, total)} of ${total}` : '0 records'}</p><Pagination className="mx-0 w-auto"><PaginationContent><PaginationItem><Button size="sm" variant="outline" disabled={!offset} onClick={() => onChange(Math.max(0, offset - 20))}>Previous</Button></PaginationItem><PaginationItem><Button size="sm" variant="outline" disabled={offset + 20 >= total} onClick={() => onChange(offset + 20)}>Next</Button></PaginationItem></PaginationContent></Pagination></div>
}

function ErrorNotice({ error }: { error: string }) {
  return error ? <Alert variant="destructive" role="alert"><AlertTitle>Connection needs attention</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null
}

function EvidenceTable({ items }: { items: Evidence[] }) {
  return <div className="space-y-3">{!items.length && <p className="text-sm text-muted-foreground">No candidate evidence recorded for this version.</p>}{items.map((e, i) => <Card key={i} className="shadow-none"><CardContent className="space-y-3"><div className="flex justify-between gap-3"><span className="uuid">{e.candidate_patient_id}</span><Badge variant="secondary">{e.score} / 24</Badge></div><p className="text-xs text-muted-foreground">Candidate record <span className="uuid">{e.candidate_record_id}</span></p><p className="text-sm">Matching tokens: {e.evidence.matched_tokens.join(', ') || 'None'}</p><div className="flex flex-wrap gap-2">{e.evidence.group_scores.map((score, g) => <Badge key={g} variant="outline">Group {g + 1}: {score}</Badge>)}</div><p className="text-xs text-muted-foreground">{human(e.evidence.classification)} · {e.scoring_version}</p></CardContent></Card>)}</div>
}

function History({ record, token }: { record: RecordInfo; token: string }) {
  const [offset, setOffset] = useState(0)
  const { data, error, loading } = useApi<Page<Version>>(`records/${record.id}/versions?offset=${offset}`, token)
  return <div className="space-y-4"><ErrorNotice error={error}/><p className="text-xs text-muted-foreground">Content last changed {date(record.content_changed_at)}</p>{loading && <Skeleton className="h-24"/>}{data?.items.map(v => <div key={v.id} className="space-y-3 rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{date(v.received_at)}</strong>{v.active_for_matching && <Badge>Used for matching</Badge>}</div><p className="text-xs text-muted-foreground">{v.token_count} populated tokens · {v.token_version} · {v.key_id}</p><EvidenceTable items={v.assessments}/></div>)}{data && <Pager offset={offset} total={data.total} onChange={setOffset}/>}</div>
}

function DetailPanel({ selection, token, close }: { selection: { kind: string; id: string }; token: string; close: () => void }) {
  const [offset, setOffset] = useState(0), [history, setHistory] = useState<RecordInfo | null>(null)
  const { data, error, loading } = useApi<Detail>(`${selection.kind}/${selection.id}?offset=${offset}`, token, 5000)
  return <Sheet open onOpenChange={open => !open && close()}><SheetContent className="w-full overflow-y-auto sm:max-w-2xl"><SheetHeader><SheetTitle>{selection.kind === 'patients' ? 'Patient linkage' : selection.kind === 'reviews' ? 'Review evidence' : 'Submission details'}</SheetTitle><SheetDescription className="uuid">{selection.id}</SheetDescription></SheetHeader><div className="space-y-6 p-6 pt-2"><ErrorNotice error={error}/>{loading && <Skeleton className="h-40"/>}{data && <>
    {data.reason && <Alert><AlertTitle className="capitalize">{human(data.reason)}</AlertTitle><AlertDescription>This record is held for review. Existing patient links have not been reassigned. Review decisions are not enabled in this release.</AlertDescription></Alert>}
    {data.error && <Alert variant="destructive"><AlertDescription>{data.error}</AlertDescription></Alert>}
    {data.members && <><p className="text-sm text-muted-foreground">Latest arrival {date(data.latest_received_at)}</p><h3 className="font-medium">Hospital memberships</h3>{data.members.map(r => <Card key={r.id} className="shadow-none"><CardContent className="space-y-3"><div className="flex justify-between gap-3"><strong>Hospital {r.site_id}</strong><Status value={r.status}/></div><p className="text-sm">Local patient <span className="uuid">{r.local_patient_id}</span></p><p className="text-xs text-muted-foreground">Latest arrival {date(r.latest_received_at)}</p><Button variant="outline" size="sm" onClick={() => setHistory(history?.id === r.id ? null : r)}>View version history</Button>{history?.id === r.id && <History record={r} token={token}/>}</CardContent></Card>)}</>}
    {data.record && <div className="rounded-lg bg-muted p-4 text-sm">Hospital {data.record.site_id} · Local patient <span className="uuid">{data.record.local_patient_id}</span></div>}
    {data.assessments && <EvidenceTable items={data.assessments}/>}
    {data.items && <><div className="flex flex-wrap gap-3"><Status value={data.status}/><Badge variant="outline">{data.row_count} records</Badge><Badge variant="outline">{data.duplicate_count} duplicates removed</Badge></div><p className="text-sm text-muted-foreground">Hospital {data.site_id} · Received {date(data.received_at)}</p>{data.items.map((r, i) => <div key={i} className="space-y-2 rounded-lg border p-4"><div className="flex justify-between gap-3"><span className="uuid">{r.local_patient_id}</span><Status value={r.status}/></div><p className="uuid">{r.global_patient_id || 'Global ID pending review'}</p><p className="text-xs text-muted-foreground">{date(r.latest_received_at)}</p></div>)}{!data.items.length && <p className="text-sm text-muted-foreground">Results appear when matching completes.</p>}<Pager offset={offset} total={data.total || 0} onChange={setOffset}/></>}
  </>}</div></SheetContent></Sheet>
}

function Dashboard({ token, logout }: { token: string; logout: () => void }) {
  const [tab, setTab] = useState('overview'), [offset, setOffset] = useState(0), [search, setSearch] = useState(''), [query, setQuery] = useState(''), [filter, setFilter] = useState('')
  const [selection, setSelection] = useState<{ kind: string; id: string } | null>(null)
  const summary = useApi<Overview>('overview', token)
  const kind = tab === 'overview' ? 'submissions' : tab
  const list = useApi<Page<Row>>(`${kind}?offset=${offset}&q=${encodeURIComponent(query)}&status=${filter}`, token, 5000)
  const navigate = (next: string) => { setTab(next); setOffset(0); setSearch(''); setQuery(''); setFilter('') }
  const stats = [
    { name: 'Global patients', value: summary.data?.patients, icon: Users, description: 'Stable identities across hospitals' },
    { name: 'Hospital records linked', value: summary.data?.linked_records, icon: Link2, description: 'Local IDs mapped to global patients' },
    { name: 'Submissions received', value: summary.data?.submissions, icon: FileStack, description: `${summary.data?.queued_submissions ?? 0} waiting for matching` },
    { name: 'Pending review', value: summary.data?.pending_reviews, icon: Clock3, description: 'Held for closer inspection' },
  ]
  return <div className="min-h-screen lg:grid lg:grid-cols-[236px_1fr]">
    <aside className="flex flex-col bg-[#183c30] p-6 text-white lg:sticky lg:top-0 lg:h-screen"><div className="flex items-center gap-3"><span className="rounded-xl bg-[#dce9bf] p-2.5 text-[#214735]"><Fingerprint size={26}/></span><div className="text-lg font-semibold tracking-tight">PPRL<span className="block text-[10px] font-normal tracking-[.22em] text-white/60">BROKER WORKSPACE</span></div></div><p className="mt-12 hidden text-[10px] tracking-[.22em] text-white/45 lg:block">RECORD LINKAGE</p><nav aria-label="Main navigation" className="mt-5 flex flex-wrap gap-2 lg:flex-col">{[{ id: 'overview', icon: Activity, title: 'Overview' }, { id: 'patients', icon: Users, title: 'Patients' }, { id: 'submissions', icon: FileStack, title: 'Submissions' }, { id: 'reviews', icon: Clock3, title: 'Review queue' }].map(n => <button key={n.id} onClick={() => navigate(n.id)} aria-current={tab === n.id ? 'page' : undefined} className={`flex items-center gap-3 rounded-lg px-3 py-3 text-left text-sm transition-colors focus-visible:outline-2 focus-visible:outline-white ${tab === n.id ? 'bg-white/12 text-white' : 'text-white/60 hover:bg-white/5 hover:text-white'}`}><n.icon size={17}/>{n.title}{n.id === 'reviews' && !!summary.data?.pending_reviews && <span className="ml-auto rounded bg-white/15 px-2 text-xs">{summary.data.pending_reviews}</span>}</button>)}</nav><div className="mt-auto hidden pt-10 lg:block"><Separator className="bg-white/15"/><div className="mt-5 flex gap-2 text-xs text-white/70"><ShieldCheck size={16}/><span>Protected tokens only</span></div><p className="mt-2 text-[11px] leading-relaxed text-white/40">Local demonstration<br/>Synthetic patient data</p></div></aside>
    <div className="min-w-0"><header className="flex flex-wrap items-center justify-between gap-3 border-b bg-white/70 px-6 py-5 lg:px-10"><div className="text-xs text-muted-foreground">Broker workspace <span className="mx-3 text-border">/</span><span className="capitalize text-foreground">{tab === 'reviews' ? 'Review queue' : tab}</span></div><div className="flex items-center gap-4"><span className="flex items-center gap-2 text-xs text-muted-foreground"><span className={`h-1.5 w-1.5 rounded-full ${summary.error ? 'bg-destructive' : 'bg-emerald-600'}`}/>{summary.error ? 'Connection interrupted' : 'Broker connected'}</span><Button variant="ghost" size="sm" onClick={logout}><LogOut/>Sign out</Button></div></header>
    <main className="mx-auto max-w-[1550px] space-y-7 px-5 py-8 lg:px-10 lg:py-10"><div className="flex flex-wrap items-end justify-between gap-4"><div><p className="mb-3 text-[10px] font-semibold tracking-[.22em] text-primary">FEDERATED PATIENT LINKAGE</p><h1 className="display-title text-4xl lg:text-5xl">{tab === 'overview' ? 'One patient. Connected records.' : tab === 'patients' ? 'Patient identities' : tab === 'submissions' ? 'Hospital submissions' : 'Evidence awaiting review'}</h1><p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">{tab === 'reviews' ? 'Inspect uncertain matches and conflicting updates. Established patient links are preserved.' : 'Follow protected submissions, understand patient links, and see what needs attention.'}</p></div><Button variant="outline" onClick={() => { summary.reload(); list.reload() }}><RefreshCw/>Refresh</Button></div>
    <ErrorNotice error={summary.error || list.error}/>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{stats.map(s => <Card key={s.name} className="gap-4 shadow-none"><CardHeader className="flex flex-row items-center justify-between"><CardTitle className="text-xs font-medium text-muted-foreground">{s.name}</CardTitle><s.icon size={17} className="text-primary/60"/></CardHeader><CardContent>{s.value === undefined ? <Skeleton className="h-10 w-16"/> : <p className="text-4xl font-medium tracking-tight">{s.value.toLocaleString()}</p>}<p className="mt-3 text-[11px] text-muted-foreground">{s.description}</p></CardContent></Card>)}</div>
    {tab === 'overview' && <div className="flex flex-wrap items-center gap-5 rounded-xl border border-[#cadcc6] bg-[#e9f0e4] p-5"><ShieldCheck className="text-primary" size={28}/><div className="flex-1"><h2 className="text-sm font-semibold">Identifiers stay with each hospital</h2><p className="mt-1 text-xs leading-relaxed text-muted-foreground">The broker links protected tokens to global patient IDs. Ambiguous evidence is held for review.</p></div><Badge variant="outline" className="border-primary/20 text-primary">Provisional scoring · 24 points</Badge></div>}
    <Card className="gap-0 overflow-hidden shadow-none"><CardHeader className="gap-5 pb-5"><div className="flex flex-wrap items-center justify-between gap-4"><div><CardTitle className="text-lg">{kind === 'patients' ? 'Patient directory' : kind === 'reviews' ? 'Open review cases' : tab === 'overview' ? 'Recent submissions' : 'Submission history'}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{kind === 'patients' ? 'Select a patient to inspect hospital memberships and history.' : kind === 'reviews' ? 'Scores indicate evidence strength, not a probability.' : 'Delivery and matching progress from participating hospitals.'}</p></div>{kind === 'patients' && <form className="flex gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); setQuery(search); setOffset(0) }}><Input aria-label="Search global or local patient ID" placeholder="Search patient ID…" value={search} onChange={e => setSearch(e.target.value)} className="w-56"/><Button variant="outline" size="icon" aria-label="Search"><Search/></Button></form>}</div>{kind === 'submissions' && <Tabs value={filter || 'all'} onValueChange={v => { setFilter(v === 'all' ? '' : v); setOffset(0) }}><TabsList><TabsTrigger value="all">All</TabsTrigger><TabsTrigger value="queued">Queued</TabsTrigger><TabsTrigger value="processed">Processed</TabsTrigger><TabsTrigger value="failed">Failed</TabsTrigger><TabsTrigger value="rejected">Rejected</TabsTrigger></TabsList></Tabs>}</CardHeader>
    {list.loading ? <div className="space-y-3 p-6">{[1, 2, 3].map(i => <Skeleton key={i} className="h-12"/>)}</div> : !list.data?.items.length ? <div className="flex flex-col items-center gap-3 border-t py-16 text-center"><Database size={28} className="text-muted-foreground/50"/><h3 className="font-medium">{kind === 'reviews' ? 'No open reviews' : 'No records to show'}</h3><p className="max-w-sm px-4 text-sm text-muted-foreground">{kind === 'reviews' ? 'Ambiguous matches will appear here for inspection.' : query || filter ? 'Try another search or filter.' : 'Send a token file from the hospital application to get started.'}</p></div> : <Table><TableHeader><TableRow>{(kind === 'patients' ? ['Global patient ID', 'Hospitals', 'Latest arrival', ''] : kind === 'reviews' ? ['Hospital / local patient', 'Reason', 'Received', ''] : ['Submission', 'Hospital', 'Records', 'Status', 'Received', '']).map((h, i) => <TableHead key={i} className="bg-muted/60 px-6 text-xs">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{list.data.items.map(r => <TableRow key={r.id}>
      {kind === 'patients' ? <><TableCell className="px-6 py-5 uuid">{r.id}</TableCell><TableCell className="px-6"><Badge variant="secondary"><Building2 size={12}/>{r.hospital_count}</Badge></TableCell><TableCell className="px-6 text-xs text-muted-foreground">{date(r.latest_received_at)}</TableCell></> : kind === 'reviews' ? <><TableCell className="px-6 py-5"><p className="text-sm">Hospital {r.record?.site_id}</p><p className="uuid mt-1 text-muted-foreground">{r.record?.local_patient_id}</p></TableCell><TableCell className="px-6 text-sm capitalize">{human(r.reason)}</TableCell><TableCell className="px-6 text-xs text-muted-foreground">{date(r.created_at)}</TableCell></> : <><TableCell className="px-6 py-5 uuid">{r.id.slice(0, 8)}…</TableCell><TableCell className="px-6 text-sm">Hospital {r.site_id}</TableCell><TableCell className="px-6 text-sm">{r.row_count}</TableCell><TableCell className="px-6"><Status value={r.status}/></TableCell><TableCell className="px-6 text-xs text-muted-foreground">{date(r.received_at)}</TableCell></>}
      <TableCell className="pr-6"><Button variant="ghost" size="sm" onClick={() => setSelection({ kind, id: r.id })} aria-label={`Inspect ${r.id}`}>Inspect<ArrowRight/></Button></TableCell></TableRow>)}</TableBody></Table>}
      {list.data && <Pager offset={offset} total={list.data.total} onChange={setOffset}/>}</Card><footer className="flex flex-wrap justify-between gap-3 border-t pt-5 text-[11px] text-muted-foreground"><span className="flex gap-2"><CheckCircle2 size={14}/>Hospital identifiers remain scoped to their organisation</span><span>Arrival times shown in your local timezone</span></footer></main></div>
      {selection && <DetailPanel key={`${selection.kind}/${selection.id}`} selection={selection} token={token} close={() => setSelection(null)}/>}
  </div>
}

export default function App() {
  const [token, setToken] = useState(''), [input, setInput] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function login(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('')
    try { await request<Overview>('overview', input, AbortSignal.timeout(15000)); setToken(input); setInput('') }
    catch { setError('Unable to connect. Check your administrator credential and that the broker is running.') }
    finally { setBusy(false) }
  }
  if (token) return <Dashboard token={token} logout={() => { setToken(''); setInput(''); setError('') }}/>
  return <main className="grid min-h-screen place-items-center p-6"><div className="w-full max-w-md"><div className="mb-8 flex items-center gap-3"><span className="rounded-xl bg-primary p-3 text-white"><Fingerprint size={28}/></span><div className="text-lg font-semibold">PPRL Broker<p className="text-xs font-normal text-muted-foreground">Protected patient linkage</p></div></div><Card className="shadow-none"><CardHeader><p className="mb-2 text-[10px] tracking-[.2em] text-primary">BROKER WORKSPACE</p><CardTitle className="display-title text-3xl font-normal">Connect to your broker</CardTitle><p className="mt-2 text-sm leading-relaxed text-muted-foreground">Inspect patient links, hospital submissions and evidence awaiting review.</p></CardHeader><CardContent><form onSubmit={login} className="space-y-5"><div className="space-y-2"><label className="text-sm font-medium" htmlFor="credential">Administrator credential</label><Input id="credential" type="password" autoComplete="off" value={input} onChange={e => setInput(e.target.value)} required placeholder="Enter your credential"/></div>{error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}<Button className="w-full" disabled={busy || !input}><LockKeyhole/>{busy ? 'Connecting…' : 'Open workspace'}<ArrowRight/></Button><p className="text-xs leading-relaxed text-muted-foreground">Your credential is held only while this page is open. Reloading signs you out.</p></form></CardContent></Card><p className="mt-6 text-center text-xs text-muted-foreground">Local demonstration · Synthetic patient data</p></div></main>
}
