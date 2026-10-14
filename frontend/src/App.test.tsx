import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const job = {
  id: 'job-1', url: 'https://project.feishu.cn/acme/issue/detail/123', status: 'review_ready',
  title: '登录后列表不刷新', created_by: 'alice', candidate: { id: 'cand-1', reproduced: true, repositories: [{ repo_id: 'server', tree_id: 'abc', status: 'verified' }] },
  evidence: { root_cause: '缓存键遗漏用户 ID', before: '回归用例失败', after: '回归用例通过' },
}

let fetchMock: ReturnType<typeof vi.fn>
beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/api/me') return json({ username: 'bob' })
    if (path === '/api/jobs' && !init?.method) return json([job])
    if (path === '/api/jobs/job-1') return json(job)
    if (path === '/api/bug-preview') return json({ title: '登录后列表不刷新', repositories: [{ id: 'server', name: 'voyager_server', reason: '工单涉及登录接口' }, { id: 'ui', name: 'operations-dashboard', reason: '工单涉及列表' }] })
    if (path === '/api/jobs' && init?.method === 'POST') return json({ id: 'job-2' }, 201)
    if (path === '/api/jobs/job-1/approve') return json({ status: 'delivering' })
    throw new Error(`unexpected request ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Bug 工作台', () => {
  it('预览飞书 Bug 后由用户确认多个推荐仓库再创建任务', async () => {
    render(<App />)
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: '新建任务' }))[0])
    await user.type(screen.getByLabelText('飞书 Bug URL'), 'https://project.feishu.cn/acme/issue/detail/123')
    await user.click(screen.getByRole('button', { name: '读取并推荐仓库' }))
    expect(await screen.findByText('工单涉及登录接口')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /operations-dashboard/ }))
    await user.click(screen.getByRole('button', { name: '创建修复任务' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ method: 'POST', body: JSON.stringify({ url: 'https://project.feishu.cn/acme/issue/detail/123', repoIds: ['server'] }) })))
  })

  it('展示修前修后证据，非发起人可审核候选', async () => {
    render(<App />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /登录后列表不刷新/ }))
    expect(await screen.findByText('缓存键遗漏用户 ID')).toBeInTheDocument()
    expect(screen.getByText('回归用例失败')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '审核通过并创建 PR' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/jobs/job-1/approve', expect.objectContaining({ method: 'POST', body: JSON.stringify({ candidateId: 'cand-1' }) })))
  })

  it('发起人不能审核自己的候选', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => String(input) === '/api/me' ? json({ username: 'alice' }) : String(input) === '/api/jobs' ? json([job]) : json(job))
    render(<App />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /登录后列表不刷新/ }))
    expect(await screen.findByText(/发起人不能审核自己的候选/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '审核通过并创建 PR' })).not.toBeInTheDocument()
  })

  it('未复现任务不显示已修复或审核入口', async () => {
    const unreproduced = { ...job, status: 'unreproduced', candidate: null, evidence: { root_cause: '线索不足' } }
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => String(input) === '/api/me' ? json({ username: 'bob' }) : String(input) === '/api/jobs' ? json([unreproduced]) : json(unreproduced))
    render(<App />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /登录后列表不刷新/ }))
    expect(await screen.findByText('未复现')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '审核通过并创建 PR' })).not.toBeInTheDocument()
    expect(within(document.body).queryByText('已验证修复')).not.toBeInTheDocument()
  })

  it('识别真实 API 的候选映射、用户 ID 和逐仓 PR', async () => {
    const actual = { ...job, created_by: 'user-bob', candidate: { id: 'cand-2', repos: { server: { tree_id: 'tree-123' } } }, repositories: [{ id: 'server', name: 'server', tree_id: 'tree-123', status: 'wip', pr_url: 'https://github.com/acme/server/pull/8' }] }
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => String(input) === '/api/me' ? json({ id: 'user-bob', username: 'bob' }) : String(input) === '/api/jobs' ? json([actual]) : json(actual))
    render(<App />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /登录后列表不刷新/ }))
    expect(await screen.findByText(/发起人不能审核自己的候选/)).toBeInTheDocument()
    expect(screen.getByText('候选树 tree-123')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看 PR/ })).toHaveAttribute('href', 'https://github.com/acme/server/pull/8')
  })
})
