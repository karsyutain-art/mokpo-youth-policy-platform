import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import './App.css'
import './Search.css'
import './Chat.css'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'
const tags = ['취업', '창업', '주거', '교육', '복지', '문화']
const regions = ['목포', '전남(목포 포함)', '전국(목포 포함)']
const profileOptions = {
  employment_status: ['미취업', '구직 중', '재직 중', '프리랜서', '창업/사업자', '학생', '기타'],
  income_band: ['중위소득 50% 이하', '중위소득 50~100%', '중위소득 100~150%', '중위소득 150% 초과', '확인 어려움'],
  education_level: ['고졸 이하', '대학 재학', '대학 휴학', '대학 졸업', '대학원', '기타'],
  household_status: ['1인 가구', '부모 동거', '부부/자녀', '한부모', '기타'],
}
const eventLabel = type => type === 'new' ? '새 공고' : type === 'deadline' ? '마감 임박' : '변경 공고'

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: 'include', headers: { 'Content-Type': 'application/json', ...options.headers }, ...options })
  if (response.status === 401) return null
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '요청을 처리하지 못했습니다.')
  return response.json()
}

function Landing({ onExplore }) {
  return <><section className="hero"><div><span className="eyebrow">MOKPO YOUTH POLICY</span><h1>목포 청년의 오늘에<br /><em>딱 맞는 정책</em>을.</h1><p>흩어진 청년 지원 정보를 모으고, 나의 조건에 맞는 공고만 골라 알려드립니다.</p><div className="hero-actions"><button className="button kakao" onClick={() => { window.location.href = `${API}/auth/kakao` }}>● 카카오로 3초 만에 시작하기</button><button className="button outline" onClick={onExplore}>로그인 없이 정책 찾기</button></div><small>로그인 후 관심 분야와 생년월일을 설정하면 맞춤 추천이 시작됩니다.</small></div><aside><span>오늘의 서비스</span><strong>정책 탐색부터<br />새 공고 알림까지</strong><div>{tags.slice(0, 4).map(tag => <b key={tag}>{tag}</b>)}</div></aside></section><section className="features">{[['01', '맞춤 추천', '연령, 목포 거주, 관심 분야를 바탕으로 정책을 추립니다.'], ['02', '변경 감지', '매일 수집해 새 공고와 수정된 내용을 찾아냅니다.'], ['03', '한눈에 확인', '지원 조건과 마감일, 원문 링크까지 한 화면에서 봅니다.']].map(([n, title, copy]) => <article key={n}><span>{n}</span><h2>{title}</h2><p>{copy}</p></article>)}</section></>
}

function Profile({ user, onDone }) {
  const queryClient = useQueryClient(); const [birthDate, setBirthDate] = useState(user.birth_date || ''); const [interests, setInterests] = useState(user.interests || [])
  const [profile, setProfile] = useState({ residency_months: user.residency_months ?? '', employment_status: user.employment_status || '', income_band: user.income_band || '', education_level: user.education_level || '', household_status: user.household_status || '' })
  const mutation = useMutation({ mutationFn: () => api('/api/me/profile', { method: 'PUT', body: JSON.stringify({ birth_date: birthDate, interests, ...profile, residency_months: profile.residency_months === '' ? null : Number(profile.residency_months), employment_status: profile.employment_status || null, income_band: profile.income_band || null, education_level: profile.education_level || null, household_status: profile.household_status || null }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['me'] }); queryClient.invalidateQueries({ queryKey: ['notifications'] }); queryClient.invalidateQueries({ queryKey: ['policy-eligibility'] }); onDone() } })
  const toggle = tag => setInterests(items => items.includes(tag) ? items.filter(item => item !== tag) : [...items, tag])
  const update = event => setProfile(current => ({ ...current, [event.target.name]: event.target.value }))
  const select = (name, label) => <label><span>{label}</span><select name={name} value={profile[name]} onChange={update}><option value="">선택하지 않음</option>{profileOptions[name].map(option => <option key={option}>{option}</option>)}</select></label>
  return <section className="profile-page wide"><div className="page-heading"><span className="eyebrow">PERSONALIZE</span><h1>나에게 맞게<br /><em>정책 추천을 설정</em>해요.</h1><p>입력한 정보는 맞춤 추천과 자격 진단에 사용되며, 알 수 없는 값은 비워둘 수 있습니다.</p></div><form className="profile-card" onSubmit={event => { event.preventDefault(); mutation.mutate() }}><div className="profile-grid"><label><span>생년월일</span><input required type="date" value={birthDate} onChange={event => setBirthDate(event.target.value)} /></label><label><span>목포 거주기간</span><input name="residency_months" type="number" min="0" max="1200" value={profile.residency_months} onChange={update} placeholder="개월 수, 예: 24" /></label>{select('employment_status', '취업 상태')}{select('income_band', '소득구간')}{select('education_level', '학력')}{select('household_status', '가구 상황')}</div><div className="residence"><span>거주 지역</span><strong>목포시</strong><small>현재 서비스는 목포 거주 청년을 대상으로 합니다.</small></div><fieldset><legend>관심 분야 <small>복수 선택 가능</small></legend><div className="interests">{tags.map(tag => <label key={tag}><input type="checkbox" checked={interests.includes(tag)} onChange={() => toggle(tag)} /><span>{tag}</span></label>)}</div></fieldset>{mutation.error && <p className="error">{mutation.error.message}</p>}<button className="button dark" disabled={mutation.isPending}>저장하고 자격 진단 준비하기</button></form></section>
}

function PolicyCard({ item, onSelect, notification = false }) {
  return <article className="policy-card">{notification ? <b className="notice">{eventLabel(item.change_type)}</b> : <div><span>{item.category} · {item.target_region}</span><span>마감 {item.application_end_date || '상시 또는 별도 확인'}</span></div>}<h2>{item.title}</h2><p>{notification ? item.match_reason : item.summary || item.content?.slice(0, 280)}</p><div className="card-actions">{onSelect && <button className="link-button" onClick={() => onSelect(item.id)}>자세히 보기 →</button>}<a href={item.original_link} target="_blank" rel="noreferrer">공고 원문 ↗</a></div></article>
}

function WishlistButton({ policyId }) {
  const queryClient = useQueryClient()
  const { data = { saved: false, notifications_enabled: false }, isLoading } = useQuery({ queryKey: ['wishlist-state', policyId], queryFn: () => api(`/api/policies/${policyId}/wishlist`) })
  const saveMutation = useMutation({ mutationFn: () => data.saved ? api(`/api/policies/${policyId}/wishlist`, { method: 'DELETE' }) : api(`/api/policies/${policyId}/wishlist`, { method: 'POST', body: JSON.stringify({ notifications_enabled: true }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['wishlist-state', policyId] }); queryClient.invalidateQueries({ queryKey: ['wishlist'] }) } })
  const alertMutation = useMutation({ mutationFn: enabled => api(`/api/policies/${policyId}/wishlist`, { method: 'POST', body: JSON.stringify({ notifications_enabled: enabled }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['wishlist-state', policyId] }); queryClient.invalidateQueries({ queryKey: ['wishlist'] }); queryClient.invalidateQueries({ queryKey: ['notification'] }) } })
  if (isLoading) return null
  return <div className="wishlist-controls"><button className={`button ${data.saved ? 'saved' : 'outline'}`} onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{data.saved ? '♥ 관심 정책 저장됨' : '♡ 관심 정책 저장'}</button>{data.saved && <label><input type="checkbox" checked={data.notifications_enabled} onChange={event => alertMutation.mutate(event.target.checked)} /> 변경·마감 알림 받기</label>}</div>
}

function Wishlist({ onSelect }) {
  const queryClient = useQueryClient(); const { data: items = [], isLoading } = useQuery({ queryKey: ['wishlist'], queryFn: () => api('/api/wishlist') })
  const remove = useMutation({ mutationFn: id => api(`/api/policies/${id}/wishlist`, { method: 'DELETE' }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['wishlist'] }) })
  const toggleAlert = useMutation({ mutationFn: ({ id, enabled }) => api(`/api/policies/${id}/wishlist`, { method: 'POST', body: JSON.stringify({ notifications_enabled: enabled }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['wishlist'] }); queryClient.invalidateQueries({ queryKey: ['notification'] }) } })
  if (isLoading) return <p className="loading">관심 정책을 불러오고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">MY WISHLIST</span><h1>관심 정책</h1><p>나중에 다시 볼 정책을 모으고 변경·마감 알림을 관리할 수 있어요.</p></div>{items.length ? <div className="policy-list">{items.map(item => <article className="policy-card wishlist-card" key={item.id}><div><span>{item.category} · {item.target_region}</span><span>마감 {item.application_end_date || '상시 또는 별도 확인'}</span></div><h2>{item.title}</h2><p>{item.summary}</p><div className="wishlist-row"><label><input type="checkbox" checked={item.notifications_enabled} onChange={event => toggleAlert.mutate({ id: item.id, enabled: event.target.checked })} /> 변경·마감 알림</label><div><button className="link-button" onClick={() => onSelect(item.id)}>자세히 보기 →</button><button className="remove-button" onClick={() => remove.mutate(item.id)}>저장 해제</button></div></div></article>)}</div> : <div className="empty"><span className="eyebrow">EMPTY WISHLIST</span><h1>저장한 관심 정책이 없어요.</h1><p>정책 상세화면에서 ‘관심 정책 저장’을 누르면 이곳에서 다시 확인할 수 있습니다.</p></div>}</section>
}

function Notifications({ onSelect }) {
  const queryClient = useQueryClient(); const { data: items = [], isLoading } = useQuery({ queryKey: ['notification'], queryFn: () => api('/api/notifications') })
  const mutation = useMutation({ mutationFn: ({ id, status }) => api(`/api/notifications/${id}`, { method: 'PUT', body: JSON.stringify({ status }) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification'] }) })
  if (isLoading) return <p className="loading">새 알림을 확인하고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">POLICY UPDATES</span><h1>새 알림</h1><p>나의 조건과 관심 분야에 맞는 신규·변경·마감 임박 정책입니다.</p></div>{items.length ? <div className="policy-list">{items.map(item => <article className="policy-card notification-card" key={item.id}><b className={`notice ${item.change_type === 'deadline' ? 'deadline' : ''}`}>{eventLabel(item.change_type)}</b><h2>{item.title}</h2><p>{item.match_reason}</p><div className="notification-actions"><button className="link-button" onClick={() => onSelect(item.policy_id)}>자세히 보기 →</button><button onClick={() => mutation.mutate({ id: item.id, status: 'notified' })}>확인 완료</button><button className="remove-button" onClick={() => mutation.mutate({ id: item.id, status: 'dismissed' })}>숨기기</button></div></article>)}</div> : <div className="empty"><span className="eyebrow">ALL CAUGHT UP</span><h1>확인할 새 알림이 없어요.</h1><p>신규·변경·마감 임박 정책을 발견하면 관심 분야와 조건을 비교해 알려드릴게요.</p></div>}</section>
}

function Search({ onSelect }) {
  const emptyFilters = { q: '', category: '', region: '', recruitment: 'open', age: '' }
  const [draft, setDraft] = useState(emptyFilters)
  const [filters, setFilters] = useState(emptyFilters)
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== '').map(([key, value]) => [key, String(value)]))
  const { data = { items: [], total: 0 }, isLoading, error } = useQuery({ queryKey: ['search', filters], queryFn: () => api(`/api/policies?${params}`) })
  const update = event => setDraft(current => ({ ...current, [event.target.name]: event.target.value }))
  const reset = () => { setDraft(emptyFilters); setFilters(emptyFilters) }
  return <section><div className="list-heading search-heading"><span className="eyebrow">POLICY FINDER</span><h1>청년정책 찾기</h1><p>목포 거주자가 신청할 수 있는 목포·전남·전국 정책을 한 번에 찾아보세요.</p></div><form className="search-panel" onSubmit={event => { event.preventDefault(); setFilters({ ...draft }) }}><label className="search-keyword"><span>검색어</span><input name="q" value={draft.q} onChange={update} placeholder="정책명, 지원 내용, 기관 검색" /></label><label><span>분야</span><select name="category" value={draft.category} onChange={update}><option value="">전체 분야</option>{tags.map(tag => <option key={tag}>{tag}</option>)}</select></label><label><span>대상 지역</span><select name="region" value={draft.region} onChange={update}><option value="">전체 지역</option>{regions.map(region => <option key={region}>{region}</option>)}</select></label><label><span>모집 상태</span><select name="recruitment" value={draft.recruitment} onChange={update}><option value="open">신청 가능</option><option value="closed">마감</option><option value="all">전체</option></select></label><label><span>나이</span><input name="age" type="number" min="0" max="120" value={draft.age} onChange={update} placeholder="예: 25" /></label><div className="search-buttons"><button className="button dark" type="submit">조건으로 검색</button><button className="button ghost" type="button" onClick={reset}>초기화</button></div></form>{error && <p className="error-box">{error.message}</p>}{isLoading ? <p className="loading">정책을 찾고 있어요…</p> : <><div className="result-summary"><strong>{data.total}</strong>개의 정책을 찾았습니다.</div>{data.items.length ? <div className="policy-list">{data.items.map(item => <PolicyCard key={item.id} item={item} onSelect={onSelect} />)}</div> : <div className="empty compact"><span className="eyebrow">NO RESULTS</span><h1>조건에 맞는 정책이 없어요.</h1><p>검색어나 필터를 조금 완화해서 다시 찾아보세요.</p><button className="link-button" onClick={reset}>검색 조건 초기화 →</button></div>}</>}</section>
}

function Chat({ onSelect, user }) {
  const [question, setQuestion] = useState(''); const [selectedResult, setSelectedResult] = useState(null); const queryClient = useQueryClient()
  const { data: history = [] } = useQuery({ queryKey: ['chat-history'], queryFn: () => api('/api/chat/history'), enabled: Boolean(user) })
  const mutation = useMutation({ mutationFn: value => api('/api/chat', { method: 'POST', body: JSON.stringify({ question: value }) }), onSuccess: data => { setSelectedResult(data); queryClient.invalidateQueries({ queryKey: ['chat-history'] }) } })
  const clearHistory = useMutation({ mutationFn: () => api('/api/chat/history', { method: 'DELETE' }), onSuccess: () => { queryClient.setQueryData(['chat-history'], []); setSelectedResult(null) } })
  const examples = ['목포 청년 주거비 지원 정책을 알려줘', '취업 준비생이 받을 수 있는 지원은?', '지금 신청 가능한 청년 지원사업은?']
  const ask = value => { const next = value.trim(); if (next.length >= 2) { setQuestion(next); mutation.mutate(next) } }
  const result = selectedResult || mutation.data
  return <section className="chat-page"><div className="list-heading"><span className="eyebrow">GROUNDED POLICY CHAT</span><h1>AI 정책상담</h1><p>수집된 공식 정책 원문에서 근거를 찾고, 확인 가능한 내용만 안내합니다.</p></div><div className="chat-layout"><aside><strong>이렇게 물어보세요</strong>{examples.map(example => <button key={example} onClick={() => ask(example)}>{example}</button>)}<small>최종 자격과 신청 일정은 반드시 공식 공고를 확인해야 합니다.</small></aside><main><form className="chat-form" onSubmit={event => { event.preventDefault(); ask(question) }}><textarea value={question} onChange={event => setQuestion(event.target.value)} placeholder="궁금한 정책이나 현재 상황을 입력하세요." maxLength="500" /><button className="button dark" disabled={mutation.isPending || question.trim().length < 2}>{mutation.isPending ? '근거 찾는 중…' : '정책 근거 검색'}</button></form>{mutation.error && <p className="error-box">{mutation.error.message}</p>}{result && <ChatAnswer result={result} onSelect={onSelect} />}</main></div>{user && <section className="chat-history"><div><span className="eyebrow">MY CHAT HISTORY</span><h2>내 상담 기록</h2><p>로그인한 계정에서 나눈 최근 상담을 다시 볼 수 있어요.</p></div>{history.length > 0 && <button className="remove-button" onClick={() => clearHistory.mutate()} disabled={clearHistory.isPending}>기록 전체 삭제</button>}{history.length > 0 ? <div>{history.map(item => <button className="history-item" key={item.id} onClick={() => { setQuestion(item.question); setSelectedResult(item) }}><strong>{item.question}</strong><span>{new Date(item.created_at).toLocaleString('ko-KR')}</span></button>)}</div> : <p className="history-empty">아직 저장된 상담 기록이 없어요.</p>}</section>}</section>
}

function ChatAnswer({ result, onSelect }) {
  return <div className={`chat-answer ${result.grounded === false ? 'no-ground' : ''}`}><span className="eyebrow">{result.generated ? 'GEMINI + POLICY SOURCES' : 'POLICY SOURCES'}</span><p>{result.answer}</p>{result.sources?.length > 0 && <div className="chat-sources"><h2>확인한 정책 근거</h2>{result.sources.map(source => <article key={source.policy_id}><div><strong>{source.title}</strong><span>관련도 {Math.round((source.score || 0) * 100)}%</span></div><p>{source.excerpt}</p><div><button className="link-button" onClick={() => onSelect(source.policy_id)}>정책 상세보기 →</button><a href={source.original_link} target="_blank" rel="noreferrer">공식 원문 ↗</a></div></article>)}</div>}</div>
}

function Eligibility({ policyId, user, onProfile }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['policy-eligibility', policyId], queryFn: () => api(`/api/policies/${policyId}/eligibility`), enabled: Boolean(user) })
  if (!user) return <section className="eligibility login-required"><span className="eyebrow">ELIGIBILITY CHECK</span><h2>로그인하면 내 조건으로 진단할 수 있어요.</h2><p>생년월일과 프로필을 바탕으로 조건별 근거를 확인합니다.</p><button className="button kakao" onClick={() => { window.location.href = `${API}/auth/kakao` }}>● 카카오로 시작하기</button></section>
  if (isLoading) return <section className="eligibility"><p>자격조건을 확인하고 있어요…</p></section>
  if (error) return <section className="eligibility"><p className="error">{error.message}</p></section>
  const statusLabel = { eligible: '조건 부합', review: '확인 필요', ineligible: '조건 불일치' }
  return <section className="eligibility"><div className="eligibility-head"><div><span className="eyebrow">ELIGIBILITY CHECK</span><h2>자격 진단 결과</h2></div><strong className={`overall ${data.overall === '신청 가능' ? 'pass' : data.overall === '대상 아님' ? 'fail' : 'review'}`}>{data.overall}</strong></div><div className="check-list">{data.checks.map(check => <article key={check.key} className={check.status}><div><strong>{check.label}</strong><span>{statusLabel[check.status]}</span></div><p>{check.detail}</p>{check.evidence && <small>원문 근거: {check.evidence}</small>}</article>)}</div><p className="diagnosis-note">{data.disclaimer}</p><button className="link-button" onClick={onProfile}>프로필 정보 수정 →</button></section>
}

function Detail({ policyId, onBack, user, onProfile }) {
  const { data: policy, isLoading, error } = useQuery({ queryKey: ['policy-detail', policyId], queryFn: () => api(`/api/policies/${policyId}`) })
  if (isLoading) return <p className="loading">정책 원문을 정리하고 있어요…</p>
  if (error) return <div className="empty"><h1>정책을 불러오지 못했습니다.</h1><p>{error.message}</p><button className="link-button" onClick={onBack}>검색으로 돌아가기 →</button></div>
  const age = policy.min_age || policy.max_age ? `${policy.min_age ?? '제한 없음'}세 ~ ${policy.max_age ?? '제한 없음'}세` : '원문 확인 필요'
  return <section className="detail-page"><button className="back-button" onClick={onBack}>← 검색 결과로</button><div className="detail-hero"><span className="eyebrow">{policy.category} · {policy.target_region}</span><h1>{policy.title}</h1><p>{policy.organization || policy.source_site}</p><div className="detail-actions"><a className="button dark" href={policy.original_link} target="_blank" rel="noreferrer">공식 공고 확인 ↗</a>{user && <WishlistButton policyId={policyId} />}</div></div><Eligibility policyId={policyId} user={user} onProfile={onProfile} /><div className="detail-grid"><aside><dl><div><dt>신청 기간</dt><dd>{policy.period || [policy.application_start_date, policy.application_end_date].filter(Boolean).join(' ~ ') || '원문 확인 필요'}</dd></div><div><dt>연령 조건</dt><dd>{age}</dd></div><div><dt>거주 조건</dt><dd>{policy.residency_condition || policy.target_condition || policy.target_region}</dd></div><div><dt>담당 기관</dt><dd>{policy.organization || '원문 확인 필요'}</dd></div><div><dt>최종 확인</dt><dd>{policy.last_verified_at ? new Date(policy.last_verified_at).toLocaleDateString('ko-KR') : '확인 정보 없음'}</dd></div></dl></aside><article className="detail-content"><section><h2>지원 내용</h2><p>{policy.content || '상세 지원 내용은 공식 공고에서 확인해 주세요.'}</p></section><section><h2>신청 자격</h2><p>{policy.qualification_text || policy.target_condition || '구체적인 자격 조건은 공식 공고에서 확인해 주세요.'}</p></section><section><h2>신청 방법</h2><p>{policy.application_method || '신청 방법은 공식 공고에서 확인해 주세요.'}</p></section><div className="official-note"><strong>신청 전 반드시 확인하세요</strong><p>이 정보는 참고용입니다. 최종 자격과 일정은 담당 기관의 공식 공고를 기준으로 확인해 주세요.</p></div></article></div></section>
}

function List({ kind, onBack, onSelect }) {
  const endpoint = kind === 'policy' ? '/api/policies/recommended' : '/api/notifications'; const { data: items = [], isLoading } = useQuery({ queryKey: [kind], queryFn: () => api(endpoint) })
  const title = kind === 'policy' ? '내게 맞는 정책' : '새 알림'; const description = kind === 'policy' ? '지금 신청 가능한 정책만 모았습니다.' : '나의 조건과 관심사에 맞춘 신규·변경 공고입니다.'
  if (isLoading) return <p className="loading">정보를 살펴보고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">{kind === 'policy' ? 'PERSONAL MATCH' : 'POLICY UPDATES'}</span><h1>{title}</h1><p>{description}</p></div>{items.length ? <div className="policy-list">{items.map((item, index) => <PolicyCard item={item} notification={kind !== 'policy'} onSelect={kind === 'policy' ? onSelect : undefined} key={`${item.id || item.title}-${index}`} />)}</div> : <div className="empty"><span className="eyebrow">ALL CAUGHT UP</span><h1>{kind === 'policy' ? '지금은 새 공고를 기다리고 있어요.' : '확인할 새 알림이 없어요.'}</h1><p>매일 자동 수집을 통해 새 정책을 발견하는 즉시 이곳에 보여드릴게요.</p><button className="link-button" onClick={onBack}>대시보드로 돌아가기 →</button></div>}</section>
}

function Dashboard({ user, setView }) {
  const { data: policies = [] } = useQuery({ queryKey: ['policy'], queryFn: () => api('/api/policies/recommended') }); const { data: notices = [] } = useQuery({ queryKey: ['notification'], queryFn: () => api('/api/notifications') }); const { data: wishlist = [] } = useQuery({ queryKey: ['wishlist'], queryFn: () => api('/api/wishlist') })
  return <><section className="dashboard-head"><div><span className="eyebrow">MY POLICY DESK</span><h1>반가워요, <em>{user.display_name}</em>님</h1><p>관심 분야 <strong>{user.interests?.join(', ') || '설정 필요'}</strong>을 기준으로 정책을 살피고 있어요.</p></div><button className="link-button" onClick={() => setView('profile')}>프로필 수정 →</button></section><section className="stats three"><button onClick={() => setView('policy')}><span>나에게 맞는 정책</span><strong>{policies.length}<small>건</small></strong><p>신청 가능한 공고 보기 →</p></button><button onClick={() => setView('wishlist')}><span>관심 정책</span><strong>{wishlist.length}<small>건</small></strong><p>저장한 정책 관리 →</p></button><button className="accent" onClick={() => setView('notification')}><span>새 알림</span><strong>{notices.length}<small>건</small></strong><p>신규·변경 공고 확인 →</p></button></section><section className="guide"><div><span className="eyebrow">POLICY SEARCH</span><h2>원하는 정책을 직접 찾아보세요.</h2><p>키워드, 분야, 지역, 나이와 모집 상태를 조합해 목포 청년 대상 정책을 검색할 수 있어요.</p></div><button className="button dark" onClick={() => setView('search')}>전체 정책 검색</button></section></>
}

export default function App() {
  const [view, setView] = useState('home'); const [policyId, setPolicyId] = useState(null); const queryClient = useQueryClient(); const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: () => api('/api/me') })
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }); queryClient.setQueryData(['me'], null); setView('home') }
  const openDetail = id => { setPolicyId(id); setView('detail'); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  if (isLoading) return <main className="shell"><p className="loading">서비스를 준비하고 있어요…</p></main>
  let content
  if (view === 'search') content = <Search onSelect={openDetail} />
  else if (view === 'chat') content = <Chat onSelect={openDetail} user={user} />
  else if (view === 'detail') content = <Detail policyId={policyId} user={user} onBack={() => setView('search')} onProfile={() => setView('profile')} />
  else if (!user) content = <Landing onExplore={() => setView('search')} />
  else if (view === 'profile') content = <Profile user={user} onDone={() => setView('home')} />
  else if (view === 'policy') content = <List kind="policy" onBack={() => setView('home')} onSelect={openDetail} />
  else if (view === 'wishlist') content = <Wishlist onSelect={openDetail} />
  else if (view === 'notification') content = <Notifications onSelect={openDetail} />
  else content = <Dashboard user={user} setView={setView} />
  return <div className="shell"><header><button className="brand" onClick={() => setView('home')}><span>M</span>목포 청년 정책</button><nav><button onClick={() => setView('search')}>정책 검색</button><button onClick={() => setView('chat')}>AI 상담</button>{user && <><button onClick={() => setView('policy')}>맞춤 정책</button><button onClick={() => setView('wishlist')}>관심 정책</button><button onClick={() => setView('notification')}>새 알림</button><button onClick={logout}>로그아웃</button></>}</nav></header><main>{content}</main><footer>목포 거주 청년을 위한 맞춤 정책 알림 서비스</footer></div>
}
