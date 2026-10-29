import {useState} from 'react'
type Data = Record<string, unknown>
const obj=(v:unknown):Data=>v&&typeof v==='object'&&!Array.isArray(v)?v as Data:{}
const items=(v:unknown):unknown[]=>Array.isArray(v)?v:[]
const text=(v:unknown)=>typeof v==='string'?v:''
const labels:Record<string,string>={information:'信息诊断',root_cause:'根因取证',business_confirmation:'确认业务变更',prd:'PRD 讨论',prd_review:'独立收敛复核',implementation:'工作区准备与修复',completed:'已完成'}
type Props={id:string;status:string;owner:boolean;workflow:Data;submit:(route:string,body:Data)=>Promise<void>}
export default function WorkflowPanel({id,status,owner,workflow:w,submit}:Props){
 const input=obj(w.input), info=obj(w.information), root=obj(w.root_cause),prd=obj(w.prd)
 const [form,setForm]=useState<Record<string,string>>(()=>Object.fromEntries(Object.entries(input).map(([k,v])=>[k,text(v)])))
 const [triggeredOn74,setTriggeredOn74]=useState(input.triggeredOn74===true)
 const [selected,setSelected]=useState<string[]>([]),[comment,setComment]=useState(''),[answer,setAnswer]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const changes=items(root.business_changes).map(obj).slice(0,3)
 const fields=[['mobi','故障 mobi'],['environment','故障环境'],['steps','复现步骤'],['expected','预期行为'],['actual','实际行为'],['traceId','工单中的 Trace ID'],['occurredAt','发生时间（含时区）'],['notes','补充说明']]
 const valid=['mobi','environment','steps','expected','actual'].every(k=>form[k]?.trim())&&!!form.reproduction&&(form.reproduction!=='intermittent'||(triggeredOn74&&!!form.traceId?.trim()))
 async function send(route:string,body:Data){setBusy(true);setError('');try{await submit(route,{expectedRevision:w.revision,...body})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const questions=[...items(info.missing),...items(info.questions)].map(text).filter(Boolean)
 const prdQuestions=items(prd.questions).map(text).filter(Boolean)
 const reviewList=items(w.prd_reviews).map(obj)
 const independent=reviewList.length?reviewList[reviewList.length-1]:obj(obj(w.prd_reviews).independent)
 return <section className="panel workflow-panel"><div className="panel-heading"><span className="kicker">WORKFLOW / 阶段与确认</span><h2>{labels[text(w.stage)]||'任务流程'}</h2></div>
 <p className="muted">版本 {String(w.revision??'—')} · 快照 {String(w.snapshot_version??'—')}</p>
 {owner&&!['review_ready','delivered','delivery_wip','wip_incomplete'].includes(status)&&<button className="secondary" disabled={busy||status==='running'} onClick={()=>void send('refresh-snapshot',{})}>重新读取工单</button>}<div className="artifact-links"><a target="_blank" rel="noreferrer" href={'/api/jobs/'+encodeURIComponent(id)+'/artifacts/snapshot'}>查看原始快照</a>{Object.keys(info).length>0&&<a target="_blank" rel="noreferrer" href={'/api/jobs/'+encodeURIComponent(id)+'/artifacts/information'}>查看信息诊断</a>}{Object.keys(root).length>0&&<a target="_blank" rel="noreferrer" href={'/api/jobs/'+encodeURIComponent(id)+'/artifacts/root-cause'}>查看根因证据</a>}{text(prd.content)&&<a target="_blank" rel="noreferrer" href={'/api/jobs/'+encodeURIComponent(id)+'/artifacts/prd'}>查看 PRD</a>}</div>
 {questions.length>0&&<div className="warning"><strong>需要补充或核对</strong><ul>{[...new Set(questions)].map(q=><li key={q}>{q}</li>)}</ul></div>}
 {status==='needs_input'&&owner&&<form onSubmit={e=>{e.preventDefault();if(valid)void send('information',{input:{...form,triggeredOn74}})}}><h3>补充故障信息</h3><div className="workflow-fields">{fields.map(([key,label])=><label key={key}>{label}<textarea placeholder={key==='mobi'?'mobi:xxxx':undefined} rows={key==='steps'?3:2} required={['mobi','environment','steps','expected','actual'].includes(key)} value={form[key]||''} onChange={e=>setForm({...form,[key]:e.target.value})}/></label>)}<label>复现类型<select required value={form.reproduction||''} onChange={e=>setForm({...form,reproduction:e.target.value})}><option value="">请选择</option><option value="always">必现</option><option value="intermittent">偶现（需 74 触发证据及 trace-id）</option></select></label>{form.reproduction==='intermittent'&&<label className="workflow-change"><input type="checkbox" checked={triggeredOn74} onChange={e=>setTriggeredOn74(e.target.checked)}/><span>已在 192.168.2.74 触发，且工单内已记录对应 trace-id</span></label>}</div><button className="primary" disabled={busy||!valid}>提交补充信息</button></form>}
 {text(root.summary||root.root_cause)&&<p>{text(root.summary||root.root_cause)}</p>}
 {w.stage==='business_confirmation'&&<div><h3>请确认关键业务变更</h3>{changes.map((change,index)=>{const cid=text(change.id);return <label className="workflow-change" key={cid||index}><input type="checkbox" disabled={!owner||busy} checked={selected.includes(cid)} onChange={e=>setSelected(e.target.checked?[...selected,cid]:selected.filter(x=>x!==cid))}/><span>{text(change.title)||text(change.description)}{!!text(change.title)&&<small>{text(change.description)}</small>}</span></label>})}{owner&&<><label>调整意见<textarea value={comment} onChange={e=>setComment(e.target.value)} /></label><div className="actions"><button className="primary" disabled={busy||!selected.length||status!=='awaiting_business_confirmation'} onClick={()=>void send('business-confirmation',{choice:'confirm',selectedChangeIds:selected,comment})}>确认所选业务变更</button><button className="secondary" disabled={busy||!comment.trim()} onClick={()=>void send('business-confirmation',{choice:'modify',selectedChangeIds:selected,comment})}>要求调整</button><button className="quiet" disabled={busy} onClick={()=>void send('business-confirmation',{choice:'reject',selectedChangeIds:[],comment})}>拒绝本次变更</button></div></>}</div>}
 {text(prd.content)&&<div><h3>PRD</h3><pre className="prd-content">{text(prd.content)}</pre><p>主 Agent 收敛：{prd.converged===true?'通过':'待收敛'}</p><p>独立复核：{independent.converged===true?'通过':w.ready_for_implementation===true?'通过':'未通过或尚未完成'}</p></div>}
 {text(independent.reason)&&<p>复核意见：{text(independent.reason)}</p>}{items(independent.questions).length>0&&<ul>{items(independent.questions).map(text).filter(Boolean).map(q=><li key={q}>{q}</li>)}</ul>}{prdQuestions.length>0&&<ul>{prdQuestions.map(q=><li key={q}>{q}</li>)}</ul>}
 {status==='awaiting_prd_input'&&owner&&<form onSubmit={e=>{e.preventDefault();if(answer.trim())void send('prd-feedback',{answer})}}><label>PRD 问题答复<textarea required value={answer} onChange={e=>setAnswer(e.target.value)}/></label><button className="primary" disabled={busy||!answer.trim()}>提交 PRD 答复</button></form>}
 {!owner&&['needs_input','awaiting_business_confirmation','awaiting_prd_input'].includes(status)&&<p className="hint">等待任务发起人补充或确认。</p>}
 {w.ready_for_implementation===true&&<p className="notice">PRD 已通过双方收敛确认，可以准备修改工作区。</p>}
 {error&&<p role="alert" className="alert">{error}</p>}
 </section>
}
