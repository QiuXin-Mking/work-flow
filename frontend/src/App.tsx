import { useCallback, useEffect, useState, type FormEvent } from 'react'
import './style.css'
import WorkflowPanel from './WorkflowPanel'

type AnyRecord = Record<string, unknown>
type Repo = { id: string; name: string; reason?: string; selected?: boolean; status?: string; tree?: string; prUrl?: string }
type Job = { id: string; title: string; url: string; status: string; createdBy: string; stage?: string; updatedAt?: string; candidate?: AnyRecord; evidence?: AnyRecord; repositories: Repo[]; events: AnyRecord[]; error?: string; workflow?: AnyRecord }
type Preview = { title: string; status?: string; repositories: Repo[]; error?: string }

const record = (value: unknown): AnyRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {}
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const string = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
const first = (data: AnyRecord, ...keys: string[]) => keys.map((key) => data[key]).find((item) => item !== undefined && item !== null)
const repoList = (value: unknown): unknown[] => Array.isArray(value) ? value : value && typeof value === 'object' ? Object.entries(value as AnyRecord).map(([id, details]) => ({ id, ...record(details) })) : []

function repoFrom(value: unknown): Repo {
  const data = record(value)
  return {
    id: string(first(data, 'id', 'repo_id', 'repoId')),
    name: string(first(data, 'name', 'display_name', 'repo_id', 'repoId', 'id')),
    reason: string(first(data, 'reason', 'recommendation_reason')) || array(first(data, 'evidence')).map(string).join(' · '),
    status: string(first(data, 'status', 'delivery_status')),
    tree: string(first(data, 'tree_id', 'tree', 'treeId')),
    prUrl: string(first(data, 'pr_url', 'prUrl', 'url')),
  }
}

function normalizePreview(value: unknown): Preview {
  const data = record(value)
  const source = record(first(data, 'bug', 'snapshot', 'work_item'))
  const repos = first(data, 'repositories', 'recommended_repos', 'repo_suggestions') ?? first(source, 'repositories', 'recommended_repos')
  return { title: string(first(data, 'title', 'name') ?? first(source, 'title', 'name')), status: string(first(data, 'status') ?? first(source, 'status')), repositories: repoList(repos).map(repoFrom) }
}

function normalizeJob(value: unknown): Job {
  const data = record(value)
  const candidate = record(first(data, 'candidate', 'current_candidate'))
  const bug = record(first(data, 'bug', 'snapshot'))
  return {
    id: string(first(data, 'id', 'job_id', 'jobId')),
    title: string(first(data, 'title') ?? first(bug, 'title')) || '待读取工单',
    url: string(first(data, 'url', 'bug_url', 'bugUrl')),
    status: string(first(data, 'status', 'state')) || 'ingesting',
    createdBy: string(first(data, 'created_by', 'createdBy', 'creator_id', 'creator', 'user_id')),
    stage: string(first(data, 'stage', 'current_stage')) || string(record(data.workflow).stage),
    workflow: Object.keys(record(data.workflow)).length ? record(data.workflow) : undefined,
    updatedAt: string(first(data, 'updated_at', 'updatedAt', 'created_at')),
    candidate: Object.keys(candidate).length ? candidate : undefined,
    evidence: record(first(data, 'evidence', 'diagnosis', 'results')),
    repositories: repoList(first(data, 'repositories', 'repos', 'repo_results') ?? first(candidate, 'repositories', 'repos') ?? first(data, 'repo_ids')).map((value) => typeof value === 'string' ? { id: value, name: value } : repoFrom(value)),
    events: array(first(data, 'events')).map(record),
    error: string(first(data, 'error', 'message')),
  }
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try { const body = await response.json(); message = string(body.detail ?? body.error ?? body.message) || message } catch { /* non-JSON errors */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

const statusLabel: Record<string, string> = {
  information: '信息诊断', root_cause: '根因取证', business_confirmation: '确认业务变更', prd: 'PRD 讨论', prd_review: '独立收敛复核', implementation: '工作区准备与修复',
  RESOLVED: '已修复', CLOSED: '已关闭', DONE: '已完成',
  ingesting: '读取工单', ready: '等待执行', queued: '等待执行', running: '执行中', needs_input: '需要补充信息',
  awaiting_business_confirmation: '待确认业务变更', awaiting_prd_input: '待答复 PRD 问题',
  review_ready: '待人工审核', delivering: '创建 PR 中', delivery_wip: '部分 PR 为 WIP', delivered: '已交付',
  failed: '失败', cancelled: '已取消', unreproduced: '未复现', diagnosis_only: '仅有诊断',
}
const statusText = (status: string) => statusLabel[status] ?? status.replaceAll('_', ' ')
const eventText = (event: AnyRecord) => string(first(event, 'message', 'summary') ?? first(record(event.payload), 'message', 'summary', 'stage') ?? first(event, 'kind', 'type', 'stage')) || '任务事件'

export default function App() {
  const [user, setUser] = useState('')
  const [userId, setUserId] = useState('')
  const [booting, setBooting] = useState(true)
  const [loginName, setLoginName] = useState('')
  const [password, setPassword] = useState('')
  const [jobs, setJobs] = useState<Job[]>([])
  const [view, setView] = useState<'list' | 'new' | 'detail'>('list')
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<Job | null>(null)
  const [url, setUrl] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [selectedRepos, setSelectedRepos] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refreshJobs = useCallback(async () => {
    const payload = await api<unknown>('/jobs')
    setJobs(array(Array.isArray(payload) ? payload : first(record(payload), 'jobs', 'items')).map(normalizeJob))
  }, [])
  const refreshDetail = useCallback(async (id: string) => {
    const payload = await api<unknown>(`/jobs/${encodeURIComponent(id)}`)
    const next = normalizeJob(payload)
    setDetail((previous) => previous?.id === id && !next.events.length ? { ...next, events: previous.events } : next)
  }, [])

  useEffect(() => {
    api<unknown>('/me').then((payload) => {
      const account = record(payload)
      setUser(string(first(account, 'username', 'name', 'id') ?? first(record(account.user), 'username', 'name', 'id')))
      setUserId(string(first(account, 'id', 'user_id') ?? first(record(account.user), 'id', 'user_id')))
    }).catch(() => { /* login view */ }).finally(() => setBooting(false))
  }, [])
  useEffect(() => { if (user) refreshJobs().catch((e) => setError(e.message)) }, [user, refreshJobs])
  useEffect(() => {
    if (!user || view !== 'detail' || !selectedId) return
    refreshDetail(selectedId).catch((e) => setError(e.message))
    const source = new EventSource(`/api/jobs/${encodeURIComponent(selectedId)}/events`)
    const onEvent = (event: Event) => {
      try {
        const incoming = record(JSON.parse((event as MessageEvent).data))
        setDetail((current) => current?.id === selectedId ? { ...current, events: [...current.events, incoming].slice(-50) } : current)
      } catch { /* a status-only event still triggers a refresh */ }
      refreshDetail(selectedId).catch(() => undefined)
      refreshJobs().catch(() => undefined)
    }
    source.addEventListener('message', onEvent)
    source.addEventListener('progress', onEvent)
    source.addEventListener('status', onEvent)
    return () => source.close()
  }, [user, view, selectedId, refreshDetail, refreshJobs])

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await api('/login', { method: 'POST', body: JSON.stringify({ username: loginName, password }) })
      const account = record(await api('/me'))
      setUser(string(first(account, 'username', 'name') ?? first(record(account.user), 'username', 'name')) || loginName)
      setUserId(string(first(account, 'id', 'user_id') ?? first(record(account.user), 'id', 'user_id')))
      setPassword('')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function loadPreview(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setPreview(null)
    try {
      const result = normalizePreview(await api('/bug-preview', { method: 'POST', body: JSON.stringify({ url: url.trim() }) }))
      setPreview(result); setSelectedRepos(result.repositories.map((repo) => repo.id))
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function createJob() {
    setBusy(true); setError('')
    try {
      const result = record(await api('/jobs', { method: 'POST', body: JSON.stringify({ url: url.trim(), repoIds: selectedRepos }) }))
      const id = string(first(result, 'id', 'job_id', 'jobId'))
      if (!id) throw new Error('服务端未返回任务 ID')
      setSelectedId(id); setView('detail'); setNotice('任务已创建，正在读取工单。')
      refreshJobs().catch(() => undefined)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function approve() {
    if (!detail?.candidate) return
    const candidateId = string(first(detail.candidate, 'id', 'candidate_id', 'candidateId'))
    if (!candidateId) { setError('缺少候选 ID，请刷新任务。'); return }
    setBusy(true); setError('')
    try {
      await api(`/jobs/${encodeURIComponent(detail.id)}/approve`, { method: 'POST', body: JSON.stringify({ candidateId }) })
      setNotice('审核已通过，正在创建 PR。'); await refreshDetail(detail.id); await refreshJobs()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function cancel() {
    if (!detail) return
    setBusy(true); setError('')
    try { await api(`/jobs/${encodeURIComponent(detail.id)}/cancel`, { method: 'POST' }); setNotice('已提交取消请求。'); await refreshDetail(detail.id) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function retry() {
    if (!detail) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/jobs/${encodeURIComponent(detail.id)}/retry`, { method: 'POST' })
      setNotice('任务已重新排队，正在准备执行。')
      await refreshDetail(detail.id); await refreshJobs()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  function openJob(id: string) { setSelectedId(id); setDetail(null); setView('detail'); setError(''); setNotice('') }
  function openNew() { setView('new'); setUrl(''); setPreview(null); setSelectedRepos([]); setError(''); setNotice('') }

  if (booting) return <main className="boot">正在载入工作台…</main>
  if (!user) return <main className="login-shell"><section className="login-card"><div className="brand-mark">π</div><p className="eyebrow">PI · BUG WORKFLOW</p><h1>修复工作台</h1><p className="muted">从飞书 Bug 到可审查的修复候选。</p><form onSubmit={login}><label>账号<input autoComplete="username" required value={loginName} onChange={(e) => setLoginName(e.target.value)} /></label><label>密码<input autoComplete="current-password" required type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>{error && <p role="alert" className="alert">{error}</p>}<button className="primary full" disabled={busy}>登录</button></form></section></main>

  const reviewable = detail?.status === 'review_ready' && !!detail.candidate && detail.createdBy !== (userId || user)
  const ownCandidate = detail?.status === 'review_ready' && !!detail.candidate && detail.createdBy === (userId || user)
  const evidence = detail?.evidence ?? {}
  const candidateRepos = repoList(first(detail?.candidate ?? {}, 'repositories', 'repos')).map(repoFrom)
  const displayedRepos = detail?.repositories.length ? detail.repositories : candidateRepos
  const canCancel = detail && ['ingesting', 'ready', 'queued', 'running', 'needs_input', 'awaiting_business_confirmation', 'awaiting_prd_input'].includes(detail.status)
  const canRetry = detail && ['failed', 'cancelled', 'unreproduced', 'diagnosis_only'].includes(detail.status)
  const resolvedPreview = !!preview?.status && ['RESOLVED', 'CLOSED', 'DONE', '已修复', '已关闭', '已完成'].includes(preview.status)

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark small">π</span><span>Bug 修复工作台<small>PI / OPERATIONS</small></span></div><nav aria-label="主导航"><button className={view === 'list' ? 'nav active' : 'nav'} onClick={() => { setView('list'); refreshJobs().catch((e) => setError(e.message)) }}>任务总览</button><button className={view === 'new' ? 'nav active' : 'nav'} onClick={openNew}>新建任务</button></nav><div className="sidebar-foot"><span className="online-dot" /> {user}<small>本地账号 · 共享工作区</small></div></aside>
    <main className="content">
      <header className="topbar"><span>工作区 / {view === 'new' ? '新建任务' : view === 'detail' ? '任务详情' : '任务总览'}</span><span className="topbar-right">飞书 Bug → Pi → PR</span></header>
      {error && <div role="alert" className="alert banner">{error}<button onClick={() => setError('')} aria-label="关闭错误">×</button></div>}
      {notice && <div role="status" className="notice banner">{notice}<button onClick={() => setNotice('')} aria-label="关闭提示">×</button></div>}
      {view === 'list' && <section className="page"><div className="page-head"><div><p className="eyebrow">QUEUE / 所有成员共享</p><h1>任务总览</h1><p className="muted">查看自动定位、验证与 PR 交付的最新进度。</p></div><button className="primary" onClick={openNew}>新建任务</button></div><div className="section-head"><h2>最近任务</h2><span>{jobs.length} 个</span></div>{jobs.length ? <div className="job-list">{jobs.map((job) => <button className="job-row" key={job.id} onClick={() => openJob(job.id)}><span className="job-icon">#{job.id.slice(0, 4)}</span><span className="job-main"><strong>{job.title}</strong><small>{job.id} · {job.repositories.length} 个仓库 · {job.createdBy || '未知发起人'}</small></span><span className={`pill ${job.status}`}>{statusText(job.status)}</span><span className="arrow">→</span></button>)}</div> : <div className="empty"><div className="empty-glyph">⌁</div><h3>还没有修复任务</h3><p>粘贴一条飞书 Bug 链接，确认仓库后开始定位。</p><button className="secondary" onClick={openNew}>创建第一条任务</button></div>}</section>}
      {view === 'new' && <section className="page narrow"><div className="page-head"><div><p className="eyebrow">NEW RUN / 01</p><h1>创建修复任务</h1><p className="muted">读取工单并推荐仓库。启动前请确认本次允许处理的范围。</p></div></div><div className="step-card"><div className="step-number">01</div><div className="step-body"><h2>粘贴飞书 Bug 链接</h2><form onSubmit={loadPreview}><label htmlFor="bug-url">飞书 Bug URL</label><div className="input-row"><input id="bug-url" type="url" required value={url} onChange={(e) => { setUrl(e.target.value); setPreview(null) }} placeholder="https://project.feishu.cn/..." /><button className="secondary" disabled={busy}>{busy ? '读取中…' : '读取并推荐仓库'}</button></div></form></div></div>{preview && <div className="step-card"><div className="step-number">02</div><div className="step-body"><p className="eyebrow">工单预览</p><h2>{preview.title || '无标题工单'}</h2>{preview.status && <p className="muted">飞书状态：{statusText(preview.status)}</p>}{resolvedPreview && <p className="warning">该工单已修复或关闭，不能重复创建修复任务。</p>}<div className="section-head compact"><h3>确认目标仓库</h3><span>已选 {selectedRepos.length} / {preview.repositories.length}</span></div>{preview.repositories.length ? <div className="repo-picks">{preview.repositories.map((repo) => <label className="repo-pick" key={repo.id}><input type="checkbox" checked={selectedRepos.includes(repo.id)} onChange={(e) => setSelectedRepos(e.target.checked ? [...selectedRepos, repo.id] : selectedRepos.filter((id) => id !== repo.id))} /><span><strong>{repo.name}</strong><small>{repo.reason || repo.id}</small></span></label>)}</div> : <p className="hint">没有推荐仓库。请联系管理员配置仓库映射后再启动。</p>}<div className="actions"><button className="primary" onClick={createJob} disabled={busy || !selectedRepos.length || resolvedPreview}>创建修复任务</button><span className="hint">创建后会重新读取并冻结工单内容。</span></div></div></div>}</section>}
      {view === 'detail' && <section className="page">{!detail ? <div className="empty">正在读取任务…</div> : <><div className="detail-hero"><div><button className="back" onClick={() => setView('list')}>← 返回任务列表</button><p className="eyebrow">JOB / {detail.id}</p><h1>{detail.title}</h1><div className="meta-line"><span className={`pill ${detail.status}`}>{statusText(detail.status)}</span><span>发起人 {detail.createdBy || '未知'}</span>{detail.stage && <span>当前阶段 {statusText(detail.stage)}</span>}</div></div>{canCancel && <button className="quiet" disabled={busy} onClick={cancel}>取消任务</button>}{canRetry && <button className="primary" disabled={busy} onClick={retry}>{busy ? '提交中…' : '重试任务'}</button>}</div><div className="detail-grid"><div className="detail-main"><section className="panel"><div className="panel-heading"><span className="kicker">SOURCE / 输入</span><h2>飞书工单</h2></div><a href={detail.url} target="_blank" rel="noreferrer" className="source-link">{detail.url || '工单链接待读取'} ↗</a>{detail.error && <p className="alert inline">{detail.error}</p>}</section>{detail.workflow && <WorkflowPanel key={detail.id+':'+String(detail.workflow.revision)} id={detail.id} status={detail.status} owner={detail.createdBy === (userId || user)} workflow={detail.workflow} submit={async(route, body)=>{try{await api('/jobs/'+encodeURIComponent(detail.id)+'/'+route,{method:'POST',body:JSON.stringify(body)});setNotice('已保存，任务将按确认后的信息继续。')}catch(e){setError((e as Error).message);throw e}finally{await refreshDetail(detail.id);await refreshJobs()}}} />}<section className="panel"><div className="panel-heading"><span className="kicker">EVIDENCE / 证据</span><h2>定位与验证</h2></div><div className="evidence-grid"><div><small>根因定位</small><p>{string(first(evidence, 'root_cause', 'rootCause', 'diagnosis')) || '等待定位结果'}</p></div><div><small>修前复现</small><p>{string(first(evidence, 'before', 'reproduction_before', 'pre_fix')) || '尚无可信修前复现证据'}</p></div><div><small>修后验证</small><p>{string(first(evidence, 'after', 'verification_after', 'post_fix')) || '等待独立验证'}</p></div></div>{['unreproduced', 'diagnosis_only'].includes(detail.status) && <div className="warning">未复现：仅能查看诊断结果，不能审核为已修复或创建 PR。</div>}</section><section className="panel"><div className="panel-heading"><span className="kicker">REPOSITORIES / 逐仓</span><h2>候选与 PR</h2></div>{displayedRepos.length ? <div className="repo-results">{displayedRepos.map((repo, index) => <div className="repo-result" key={repo.id || index}><div><strong>{repo.name || repo.id}</strong><small>{repo.tree ? `候选树 ${repo.tree.slice(0, 12)}` : '等待候选冻结'}</small></div><div className="repo-right"><span className="pill subtle">{repo.status ? statusText(repo.status) : '待处理'}</span>{repo.prUrl && <a href={repo.prUrl} target="_blank" rel="noreferrer">查看 PR ↗</a>}</div></div>)}</div> : <p className="muted">仓库结果尚未生成。</p>}</section></div><aside className="detail-side"><section className="panel review-panel"><span className="kicker">REVIEW / 人工门槛</span><h2>候选审核</h2>{reviewable ? <><p>请核对根因、修前失败、修后通过及所有仓库改动。通过后开始创建 PR。</p><button className="primary full" disabled={busy} onClick={approve}>审核通过并创建 PR</button></> : ownCandidate ? <p>发起人不能审核自己的候选。请另一位成员审阅。</p> : <p>{detail.status === 'review_ready' ? '候选尚未准备好。' : '独立验证通过后，非发起人可在这里审核。'}</p>}</section><section className="panel timeline-panel"><span className="kicker">ACTIVITY / 实时事件</span><h2>执行记录</h2>{detail.events.length ? <ol className="timeline">{detail.events.slice(-12).reverse().map((event, index) => <li key={string(first(event, 'id', 'seq')) || index}><span>{eventText(event)}</span><small>{string(first(event, 'created_at', 'timestamp', 'time'))}</small></li>)}</ol> : <p className="muted">等待任务事件。页面会实时更新。</p>}</section></aside></div></>}</section>}
    </main>
  </div>
}
