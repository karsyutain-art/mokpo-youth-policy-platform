import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import './App.css'
import './Home.css'
import './Search.css'
import './Chat.css'
import './Preparation.css'
import './Admin.css'

// 개발 서버(5173)에서는 FastAPI를 직접 사용하고, 배포본에서는 같은 도메인의 프록시를 사용한다.
const API = import.meta.env.VITE_API_BASE_URL ?? (window.location.port === '5173' ? 'http://localhost:5000' : '')
const tags = ['취업', '창업', '주거', '교육', '복지', '문화']
const regions = ['목포', '전남(목포 포함)', '전국(목포 포함)']
const profileOptions = {
  employment_status: ['미취업', '구직 중', '재직 중', '프리랜서', '창업/사업자', '학생', '기타'],
  income_band: ['중위소득 50% 이하', '중위소득 50~100%', '중위소득 100~150%', '중위소득 150% 초과', '확인 어려움'],
  education_level: ['고졸 이하', '대학 재학', '대학 휴학', '대학 졸업', '대학원', '기타'],
  household_status: ['1인 가구', '부모 동거', '부부/자녀', '한부모', '기타'],
}
const eventLabel = type => type === 'new' ? '새 공고' : type === 'deadline' ? '마감 임박' : '변경 공고'
const tagMeta = {
  '전체': { icon: '✦', label: '전체 정책' },
  '취업': { icon: '◎', label: '일자리' },
  '창업': { icon: '◇', label: '창업' },
  '주거': { icon: '⌂', label: '주거' },
  '교육': { icon: '▤', label: '교육·직업' },
  '복지': { icon: '＋', label: '금융·복지' },
  '문화': { icon: '◌', label: '문화·생활' },
}

const compactText = (value, limit = 180) => {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit).trim()}…` : text
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: 'include', headers: { 'Content-Type': 'application/json', ...options.headers }, ...options })
  if (response.status === 401) return null
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '요청을 처리하지 못했습니다.')
  return response.json()
}

function MainPolicyCard({ item, index, onSelect }) {
  const age = item.min_age || item.max_age ? `만 ${item.min_age ?? 0}~${item.max_age ?? '제한 없음'}세` : '연령은 공고 확인'
  const period = item.period || [item.application_start_date, item.application_end_date].filter(Boolean).join(' ~ ') || '상시 또는 별도 공고'
  const audience = compactText(item.qualification_text || item.target_condition || item.residency_condition || `${item.target_region} 거주 청년`, 110)
  const summary = compactText(item.summary || item.content || '구체적인 지원 내용은 공식 공고에서 확인할 수 있습니다.')
  return <article className={`home-policy-card tone-${index % 4}`}>
    <div className="card-poster"><div><span>{item.category}</span><b>{item.target_region}</b></div><i aria-hidden="true">{tagMeta[item.category]?.icon || '✦'}</i><h3>{item.title}</h3></div>
    <div className="home-card-content"><p>{summary}</p><dl><div><dt>지원 대상</dt><dd>{age} · {audience}</dd></div><div><dt>신청 기간</dt><dd>{compactText(period, 90)}</dd></div></dl><footer><span>{item.organization || item.source_site || '담당기관 확인'}</span><button onClick={() => onSelect(item.id)}>자세히 보기 <b>→</b></button></footer></div>
  </article>
}

function Home({ user, setView, onSelect, onSearch }) {
  const [selectedTag, setSelectedTag] = useState('전체')
  const [keyword, setKeyword] = useState('')
  const params = new URLSearchParams({ recruitment: 'open', limit: '12' })
  if (selectedTag !== '전체') params.set('category', selectedTag)
  const { data = { items: [], total: 0 }, isLoading, error } = useQuery({ queryKey: ['home-policies', selectedTag], queryFn: () => api(`/api/policies?${params}`) })
  const visibleTags = ['전체', ...tags]
  return <div className="home-page">
    <section className="home-hero"><div className="home-hero-copy"><span className="eyebrow">MOKPO YOUTH POLICY</span><h1><em>{user ? `${user.display_name}님,` : '목포 청년의'}</em><br />오늘 필요한 정책을 찾아보세요.</h1><p>목포시부터 전남·전국 정책까지, 목포에 사는 청년이 신청할 수 있는 지원 정보를 한곳에 모았습니다.</p><form className="home-search" onSubmit={event => { event.preventDefault(); onSearch(keyword) }}><input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="정책명, 지원금, 취업·주거 조건을 검색해 보세요" aria-label="정책 검색어" /><button type="submit">검색</button></form><div className="home-hero-actions">{!user ? <button className="button kakao" onClick={() => { window.location.href = `${API}/auth/kakao` }}>● 카카오로 맞춤 정책 시작하기</button> : <button className="button dark" onClick={() => setView('policy')}>나에게 맞는 정책 보기</button>}<button className="button outline" onClick={() => onSearch('')}>전체 조건 검색</button></div></div>
      <aside className="home-service-card"><small>매일 업데이트되는 정책 정보</small><strong>찾고, 비교하고,<br />신청 준비까지 한 번에</strong><ul><li><b>01</b><span>목포 거주자 대상 정책 선별</span></li><li><b>02</b><span>신규·변경·마감 공고 알림</span></li><li><b>03</b><span>근거 기반 AI 정책상담</span></li></ul></aside>
    </section>
    <section className="home-filter-section"><div className="home-section-heading"><div><span className="eyebrow">POLICY BY INTEREST</span><h2>관심 분야별 정책</h2><p>분야를 선택하면 해당 정책만 바로 모아볼 수 있어요.</p></div><button className="link-button" onClick={() => onSearch('')}>전체 정책 검색 →</button></div><div className="home-tags" role="tablist" aria-label="정책 분야">{visibleTags.map(tag => <button role="tab" aria-selected={selectedTag === tag} className={selectedTag === tag ? 'active' : ''} key={tag} onClick={() => setSelectedTag(tag)}><span>{tagMeta[tag].icon}</span>{tagMeta[tag].label}</button>)}</div></section>
    <section className="home-alert"><div><small>놓치기 쉬운 맞춤 알림</small><strong>관심 정책을 저장하면 새 공고와 마감 소식을 알려드려요.</strong></div><button onClick={() => user ? setView('notification') : window.location.href = `${API}/auth/kakao`}>{user ? '내 알림 확인하기' : '로그인하고 알림 받기'} →</button></section>
    <section className="home-policy-section"><div className="home-section-heading"><div><span className="eyebrow">OPEN NOW</span><h2>{tagMeta[selectedTag].label} · 지금 확인할 정책</h2><p>카드에서 지원 내용과 대상, 신청 기간을 먼저 확인하세요.</p></div><span className="policy-total">총 <strong>{data.total}</strong>건</span></div>{error && <p className="error-box">{error.message}</p>}{isLoading ? <p className="loading">정책을 불러오고 있어요…</p> : data.items.length ? <div className="home-policy-grid">{data.items.map((item, index) => <MainPolicyCard key={item.id} item={item} index={index} onSelect={onSelect} />)}</div> : <div className="home-empty"><strong>현재 확인할 수 있는 {tagMeta[selectedTag].label} 정책이 없습니다.</strong><p>다른 분야를 선택하거나 전체 정책 검색에서 모집 상태를 조정해 보세요.</p></div>}</section>
  </div>
}

function Profile({ user, onDone }) {
  const queryClient = useQueryClient(); const [birthDate, setBirthDate] = useState(user.birth_date || ''); const [interests, setInterests] = useState(user.interests || [])
  const [profile, setProfile] = useState({ residency_months: user.residency_months ?? '', employment_status: user.employment_status || '', income_band: user.income_band || '', education_level: user.education_level || '', household_status: user.household_status || '', legal_name: user.legal_name || '', phone_number: user.phone_number || '', postal_code: user.postal_code || '', address_line1: user.address_line1 || '', address_line2: user.address_line2 || '' })
  const mutation = useMutation({ mutationFn: () => api('/api/me/profile', { method: 'PUT', body: JSON.stringify({ birth_date: birthDate, interests, ...profile, residency_months: profile.residency_months === '' ? null : Number(profile.residency_months), employment_status: profile.employment_status || null, income_band: profile.income_band || null, education_level: profile.education_level || null, household_status: profile.household_status || null }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['me'] }); queryClient.invalidateQueries({ queryKey: ['notifications'] }); queryClient.invalidateQueries({ queryKey: ['policy-eligibility'] }); onDone() } })
  const toggle = tag => setInterests(items => items.includes(tag) ? items.filter(item => item !== tag) : [...items, tag])
  const update = event => setProfile(current => ({ ...current, [event.target.name]: event.target.value }))
  const select = (name, label) => <label><span>{label}</span><select name={name} value={profile[name]} onChange={update}><option value="">선택하지 않음</option>{profileOptions[name].map(option => <option key={option}>{option}</option>)}</select></label>
  return <section className="profile-page wide"><div className="page-heading"><span className="eyebrow">PERSONALIZE</span><h1>나에게 맞게<br /><em>정책 추천을 설정</em>해요.</h1><p>입력한 정보는 맞춤 추천과 자격 진단에 사용되며, 알 수 없는 값은 비워둘 수 있습니다.</p></div><form className="profile-card" onSubmit={event => { event.preventDefault(); mutation.mutate() }}><div className="profile-grid"><label><span>생년월일</span><input required type="date" value={birthDate} onChange={event => setBirthDate(event.target.value)} /></label><label><span>목포 거주기간</span><input name="residency_months" type="number" min="0" max="1200" value={profile.residency_months} onChange={update} placeholder="개월 수, 예: 24" /></label>{select('employment_status', '취업 상태')}{select('income_band', '소득구간')}{select('education_level', '학력')}{select('household_status', '가구 상황')}</div><fieldset className="autofill-profile"><legend>신청서 자동 채움용 선택 정보 <small>문항별 동의 시에만 반영됩니다.</small></legend><div className="profile-grid"><label><span>실명</span><input name="legal_name" value={profile.legal_name} onChange={update} /></label><label><span>연락처</span><input name="phone_number" value={profile.phone_number} onChange={update} placeholder="010-0000-0000" /></label><label><span>우편번호</span><input name="postal_code" value={profile.postal_code} onChange={update} /></label><label><span>주소</span><input name="address_line1" value={profile.address_line1} onChange={update} /></label><label><span>상세 주소</span><input name="address_line2" value={profile.address_line2} onChange={update} /></label></div></fieldset><div className="residence"><span>거주 지역</span><strong>목포시</strong><small>현재 서비스는 목포 거주 청년을 대상으로 합니다.</small></div><fieldset><legend>관심 분야 <small>복수 선택 가능</small></legend><div className="interests">{tags.map(tag => <label key={tag}><input type="checkbox" checked={interests.includes(tag)} onChange={() => toggle(tag)} /><span>{tag}</span></label>)}</div></fieldset>{mutation.error && <p className="error">{mutation.error.message}</p>}<button className="button dark" disabled={mutation.isPending}>저장하고 자격 진단 준비하기</button></form></section>
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

function PreparationButton({ policyId, onOpen }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => api(`/api/policies/${policyId}/preparations`, { method: 'POST' }),
    onSuccess: data => {
      queryClient.invalidateQueries({ queryKey: ['preparations'] })
      onOpen(data.id)
    },
  })
  return <button className="button prepare" onClick={() => mutation.mutate()} disabled={mutation.isPending}>{mutation.isPending ? '준비함 만드는 중…' : '신청 준비 시작'}</button>
}

function Wishlist({ onSelect }) {
  const queryClient = useQueryClient(); const { data: items = [], isLoading } = useQuery({ queryKey: ['wishlist'], queryFn: () => api('/api/wishlist') })
  const remove = useMutation({ mutationFn: id => api(`/api/policies/${id}/wishlist`, { method: 'DELETE' }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['wishlist'] }) })
  const toggleAlert = useMutation({ mutationFn: ({ id, enabled }) => api(`/api/policies/${id}/wishlist`, { method: 'POST', body: JSON.stringify({ notifications_enabled: enabled }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['wishlist'] }); queryClient.invalidateQueries({ queryKey: ['notification'] }) } })
  if (isLoading) return <p className="loading">관심 정책을 불러오고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">MY WISHLIST</span><h1>관심 정책</h1><p>나중에 다시 볼 정책을 모으고 변경·마감 알림을 관리할 수 있어요.</p></div>{items.length ? <div className="policy-list">{items.map(item => <article className="policy-card wishlist-card" key={item.id}><div><span>{item.category} · {item.target_region}</span><span>마감 {item.application_end_date || '상시 또는 별도 확인'}</span></div><h2>{item.title}</h2><p>{item.summary}</p><div className="wishlist-row"><label><input type="checkbox" checked={item.notifications_enabled} onChange={event => toggleAlert.mutate({ id: item.id, enabled: event.target.checked })} /> 변경·마감 알림</label><div><button className="link-button" onClick={() => onSelect(item.id)}>자세히 보기 →</button><button className="remove-button" onClick={() => remove.mutate(item.id)}>저장 해제</button></div></div></article>)}</div> : <div className="empty"><span className="eyebrow">EMPTY WISHLIST</span><h1>저장한 관심 정책이 없어요.</h1><p>정책 상세화면에서 ‘관심 정책 저장’을 누르면 이곳에서 다시 확인할 수 있습니다.</p></div>}</section>
}

function urlBase64ToUint8Array(value) {
  const padded = (value + '='.repeat((4 - value.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  return Uint8Array.from(atob(padded), character => character.charCodeAt(0))
}

function PushSettings() {
  const [message, setMessage] = useState('')
  const [subscribed, setSubscribed] = useState(false)
  const { data: config, isLoading } = useQuery({ queryKey: ['push-config'], queryFn: () => api('/api/push/public-key') })
  const supported = typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  useEffect(() => {
    if (supported) navigator.serviceWorker.getRegistration('/push-sw.js').then(async registration => setSubscribed(Boolean(await registration?.pushManager.getSubscription()))).catch(() => setSubscribed(false))
  }, [supported])
  const subscribe = useMutation({
    mutationFn: async () => {
      if (!supported) throw new Error('이 브라우저는 웹 푸시 알림을 지원하지 않습니다.')
      if (!config?.enabled) throw new Error('서버의 웹 푸시 키가 아직 설정되지 않았습니다.')
      const permission = await Notification.requestPermission()
      if (permission !== 'granted') throw new Error('브라우저 알림 권한이 허용되지 않았습니다.')
      const registration = await navigator.serviceWorker.register('/push-sw.js')
      const existing = await registration.pushManager.getSubscription()
      const subscription = existing || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(config.public_key) })
      await api('/api/push/subscriptions', { method: 'POST', body: JSON.stringify(subscription.toJSON()) })
      return true
    },
    onSuccess: () => { setSubscribed(true); setMessage('이 브라우저에서 정책 알림을 받습니다.') },
    onError: error => setMessage(error.message),
  })
  const unsubscribe = useMutation({
    mutationFn: async () => {
      const registration = await navigator.serviceWorker.getRegistration('/push-sw.js')
      const subscription = await registration?.pushManager.getSubscription()
      if (subscription) {
        await api('/api/push/subscriptions', { method: 'DELETE', body: JSON.stringify(subscription.toJSON()) })
        await subscription.unsubscribe()
      }
    },
    onSuccess: () => { setSubscribed(false); setMessage('이 브라우저의 웹 푸시 알림을 해제했습니다.') },
    onError: error => setMessage(error.message),
  })
  if (!supported) return <aside className="push-settings"><strong>브라우저 알림을 지원하지 않는 환경입니다.</strong><p>Chrome·Edge 등 HTTPS 환경에서 이용할 수 있습니다.</p></aside>
  if (isLoading) return null
  return <aside className="push-settings"><div><strong>브라우저 알림</strong><p>새 공고와 마감 임박 정책을 이 기기에서 바로 받아보세요.</p></div>{config?.enabled ? <button className="button outline" onClick={() => subscribed ? unsubscribe.mutate() : subscribe.mutate()} disabled={subscribe.isPending || unsubscribe.isPending}>{subscribed ? '브라우저 알림 해제' : '브라우저 알림 받기'}</button> : <small>운영자가 웹 푸시 설정을 준비하고 있습니다.</small>}{message && <small>{message}</small>}</aside>
}

function Notifications({ onSelect }) {
  const queryClient = useQueryClient(); const { data: items = [], isLoading } = useQuery({ queryKey: ['notification'], queryFn: () => api('/api/notifications') })
  const mutation = useMutation({ mutationFn: ({ id, status }) => api(`/api/notifications/${id}`, { method: 'PUT', body: JSON.stringify({ status }) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification'] }) })
  if (isLoading) return <p className="loading">새 알림을 확인하고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">POLICY UPDATES</span><h1>새 알림</h1><p>나의 조건과 관심 분야에 맞는 신규·변경·마감 임박 정책입니다.</p></div><PushSettings />{items.length ? <div className="policy-list">{items.map(item => <article className="policy-card notification-card" key={item.id}><b className={`notice ${item.change_type === 'deadline' ? 'deadline' : ''}`}>{eventLabel(item.change_type)}</b><h2>{item.title}</h2><p>{item.match_reason}</p><div className="notification-actions"><button className="link-button" onClick={() => onSelect(item.policy_id)}>자세히 보기 →</button><button onClick={() => mutation.mutate({ id: item.id, status: 'notified' })}>확인 완료</button><button className="remove-button" onClick={() => mutation.mutate({ id: item.id, status: 'dismissed' })}>숨기기</button></div></article>)}</div> : <div className="empty"><span className="eyebrow">ALL CAUGHT UP</span><h1>확인할 새 알림이 없어요.</h1><p>신규·변경·마감 임박 정책을 발견하면 관심 분야와 조건을 비교해 알려드릴게요.</p></div>}</section>
}

function Admin() {
  const queryClient = useQueryClient()
  const { data: overview, isLoading, error } = useQuery({ queryKey: ['admin-overview'], queryFn: () => api('/api/admin/overview') })
  const { data: pending = [] } = useQuery({ queryKey: ['admin-policies'], queryFn: () => api('/api/admin/policies?status=pending') })
  const review = useMutation({ mutationFn: ({ id, review_status, is_public }) => api(`/api/admin/policies/${id}`, { method: 'PATCH', body: JSON.stringify({ review_status, is_public }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['admin-overview'] }); queryClient.invalidateQueries({ queryKey: ['admin-policies'] }); queryClient.invalidateQueries({ queryKey: ['search'] }) } })
  if (isLoading) return <p className="loading">수집 현황을 불러오고 있어요…</p>
  if (error) return <p className="error-box">{error.message}</p>
  return <section className="admin-page"><div className="list-heading"><span className="eyebrow">ADMIN CONSOLE</span><h1>정책 검수·수집 현황</h1><p>자동 수집 결과를 확인하고 공개할 정책을 결정합니다.</p></div><div className="admin-stats">{overview.policy_counts.map(item => <article key={`${item.review_status}-${item.is_public}`}><span>{item.review_status === 'pending' ? '검수 대기' : item.review_status === 'approved' ? '승인' : '반려'} {item.is_public ? '공개' : '비공개'}</span><strong>{item.count}건</strong></article>)}</div><section className="admin-section"><h2>검수 대기 정책</h2>{pending.length ? pending.map(item => <article className="admin-policy" key={item.id}><div><span>{item.source_site} · {item.target_region}</span><h3>{item.title}</h3><p>{item.summary}</p><a href={item.original_link} target="_blank" rel="noreferrer">공식 원문 열기 ↗</a></div><div className="admin-actions"><button className="button dark" onClick={() => review.mutate({ id: item.id, review_status: 'approved', is_public: true })}>승인·공개</button><button className="button outline" onClick={() => review.mutate({ id: item.id, review_status: 'rejected', is_public: false })}>반려·비공개</button></div></article>) : <p className="admin-empty">현재 검수 대기 정책이 없습니다.</p>}</section><section className="admin-section"><h2>수집 사이트 현황</h2><div className="admin-table">{overview.sources.map(item => <div key={item.source_site}><strong>{item.source_site}</strong><span>{item.count}건 · 마지막 수집 {item.last_seen_at ? new Date(item.last_seen_at).toLocaleString('ko-KR') : '없음'}</span></div>)}</div></section><section className="admin-section"><h2>최근 실행 기록</h2><div className="admin-table">{overview.runs.length ? overview.runs.map(item => <div key={item.id}><strong className={item.status}>{item.status === 'succeeded' ? '성공' : item.status === 'failed' ? '실패' : '실행 중'}</strong><span>{item.run_type === 'collection' ? '수집' : '후처리'} · {new Date(item.started_at).toLocaleString('ko-KR')} {item.message ? `· ${item.message}` : ''}</span></div>) : <p className="admin-empty">아직 기록된 수집 실행이 없습니다.</p>}</div></section></section>
}

const requirementStatus = {
  not_started: '미준비',
  in_progress: '준비 중',
  completed: '완료',
  not_applicable: '해당 없음',
}

function Preparations({ onOpen, onSelect }) {
  const { data: items = [], isLoading, error } = useQuery({ queryKey: ['preparations'], queryFn: () => api('/api/preparations') })
  if (isLoading) return <p className="loading">신청 준비함을 불러오고 있어요…</p>
  if (error) return <p className="error-box">{error.message}</p>
  return <section><div className="list-heading"><span className="eyebrow">APPLICATION DESK</span><h1>신청 준비함</h1><p>정책별 준비서류를 확인하고 완료 상태를 이어서 관리할 수 있어요.</p></div>{items.length ? <div className="preparation-list">{items.map(item => { const percent = item.total_count ? Math.round(item.completed_count / item.total_count * 100) : 0; return <article className="preparation-card" key={item.id}><div className="preparation-card-head"><div><span>{item.organization || '담당 기관 확인 필요'}</span><h2>{item.current_policy_title}</h2></div>{item.policy_changed && <b>공고 변경 확인 필요</b>}</div><div className="progress-row"><div><i style={{ width: `${percent}%` }} /></div><strong>{item.completed_count}/{item.total_count} 완료</strong></div><p>최근 수정 {new Date(item.updated_at).toLocaleString('ko-KR')}</p><div className="card-actions"><button className="button dark" onClick={() => onOpen(item.id)}>계속 준비하기</button><button className="link-button" onClick={() => onSelect(item.policy_id)}>정책 상세보기 →</button></div></article> })}</div> : <div className="empty"><span className="eyebrow">EMPTY APPLICATION DESK</span><h1>진행 중인 신청 준비가 없어요.</h1><p>정책 상세화면에서 ‘신청 준비 시작’을 누르면 준비서류 체크리스트가 생성됩니다.</p></div>}</section>
}

function RequirementItem({ preparationId, item }) {
  const queryClient = useQueryClient()
  const update = useMutation({ mutationFn: changes => api(`/api/preparations/${preparationId}/requirements/${item.id}`, { method: 'PUT', body: JSON.stringify(changes) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }); queryClient.invalidateQueries({ queryKey: ['preparations'] }) } })
  const remove = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/requirements/${item.id}`, { method: 'DELETE' }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }); queryClient.invalidateQueries({ queryKey: ['preparations'] }) } })
  const sourceLabel = item.source_type === 'extracted' ? `자동 추출 후보${item.extraction_confidence ? ` · 신뢰도 ${Math.round(item.extraction_confidence * 100)}%` : ''}` : item.source_type === 'manual' ? '직접 추가' : '기본 체크리스트'
  return <article className={`requirement-item ${item.preparation_status}`}><div className="requirement-main"><div><span>{item.is_required ? '필수 확인' : '선택'} · {sourceLabel}</span><h3>{item.title}</h3></div><select value={item.preparation_status} onChange={event => update.mutate({ preparation_status: event.target.value })} disabled={update.isPending}>{Object.entries(requirementStatus).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>{item.evidence_text && <p className="evidence">원문 근거 · {item.evidence_text}</p>}<div className="requirement-meta">{item.issuing_organization && <span>발급처 {item.issuing_organization}</span>}{item.validity_text && <span>유효기간 {item.validity_text}</span>}{item.submission_format && <span>형식 {item.submission_format}</span>}</div><label className="note-field"><span>내 메모</span><input defaultValue={item.user_note || ''} placeholder="준비 상황이나 확인할 내용을 적어두세요." onBlur={event => { if (event.target.value !== (item.user_note || '')) update.mutate({ user_note: event.target.value || null }) }} /></label><div className="requirement-actions"><label><input type="checkbox" checked={item.user_confirmed} onChange={event => update.mutate({ user_confirmed: event.target.checked })} /> 원문과 대조해 확인함</label><button className="remove-button" onClick={() => remove.mutate()} disabled={remove.isPending}>항목 삭제</button></div></article>
}

const autofillOptions = [['', '직접 입력'], ['legal_name', '실명'], ['birth_date', '생년월일'], ['phone_number', '연락처'], ['postal_code', '우편번호'], ['address_line1', '주소'], ['address_line2', '상세 주소'], ['education_level', '학력'], ['employment_status', '취업 상태']]
function FormFieldItem({ preparationId, item }) {
  const queryClient = useQueryClient(); const [notice, setNotice] = useState(''); const update = useMutation({ mutationFn: changes => api(`/api/preparations/${preparationId}/form-fields/${item.id}`, { method: 'PUT', body: JSON.stringify(changes) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }) }); const remove = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/form-fields/${item.id}`, { method: 'DELETE' }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }) }); const draft = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/form-fields/${item.id}/draft`, { method: 'POST', body: JSON.stringify({}) }), onSuccess: result => { setNotice(result.notice); update.mutate({ value_text: result.draft }) } })
  const Control = item.field_type === 'textarea' ? 'textarea' : 'input'
  return <article className="form-field-item"><div><strong>{item.label}</strong>{item.is_required && <span>필수</span>}{item.source_type === 'extracted' && <b>공고에서 찾음</b>}{item.auto_filled && <b>동의한 프로필 값 적용</b>}</div>{item.source_evidence && <p className="evidence">원문 근거 · {item.source_evidence}</p>}<Control defaultValue={item.value_text || ''} maxLength={item.max_length || undefined} onBlur={event => { if (event.target.value !== (item.value_text || '')) update.mutate({ value_text: event.target.value || null }) }} />{item.max_length && <small>최대 {item.max_length}자</small>}{item.field_type === 'textarea' && <button className="button outline small" onClick={() => draft.mutate()} disabled={draft.isPending}>{draft.isPending ? '초안 만드는 중…' : 'AI 초안 만들기'}</button>}{notice && <small>{notice}</small>}<div className="requirement-actions"><label><input type="checkbox" checked={item.user_confirmed} onChange={event => update.mutate({ user_confirmed: event.target.checked })} /> 내용 확인함</label><button className="remove-button" onClick={() => remove.mutate()}>문항 삭제</button></div></article>
}

function PreparationQualityPanel({ preparationId }) {
  const queryClient = useQueryClient()
  const { data: validation } = useQuery({ queryKey: ['preparation-validation', preparationId], queryFn: () => api(`/api/preparations/${preparationId}/validation`) })
  const { data: versions = [] } = useQuery({ queryKey: ['preparation-versions', preparationId], queryFn: () => api(`/api/preparations/${preparationId}/versions`) })
  const refresh = () => { queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }); queryClient.invalidateQueries({ queryKey: ['preparation-validation', preparationId] }); queryClient.invalidateQueries({ queryKey: ['preparation-versions', preparationId] }) }
  const save = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/versions`, { method: 'POST', body: JSON.stringify({ label: '현재 상태 저장' }) }), onSuccess: refresh })
  const restore = useMutation({ mutationFn: id => api(`/api/preparations/${preparationId}/versions/${id}/restore`, { method: 'POST' }), onSuccess: refresh })
  return <section className="quality-panel"><div><h2>제출 전 점검</h2><p>{validation?.ready ? '필수 점검을 통과했습니다. 최종 제출 전 공식 원문을 한 번 더 확인하세요.' : '아래 항목을 확인한 뒤 HWPX를 내보내세요.'}</p></div>{validation?.errors?.map(item => <p className="validation error" key={item.key}>오류 · {item.message}</p>)}{validation?.warnings?.map(item => <p className="validation warning" key={item.key}>권고 · {item.message}</p>)}<div className="version-row"><button className="button outline small" onClick={() => save.mutate()} disabled={save.isPending}>현재 상태 저장</button>{versions.map(version => <button className="link-button" key={version.id} onClick={() => { if (window.confirm('이 저장 버전으로 복구할까요? 현재 작성 내용은 덮어써집니다.')) restore.mutate(version.id) }}>{new Date(version.created_at).toLocaleString('ko-KR')} 복구</button>)}</div></section>
}

function PreparationDetail({ preparationId, onBack }) {
  const queryClient = useQueryClient(); const [title, setTitle] = useState(''); const [fieldLabel, setFieldLabel] = useState(''); const [autofillKey, setAutofillKey] = useState(''); const [consent, setConsent] = useState(false)
  const { data, isLoading, error } = useQuery({ queryKey: ['preparation', preparationId], queryFn: () => api(`/api/preparations/${preparationId}`) })
  const refresh = () => { queryClient.invalidateQueries({ queryKey: ['preparation', preparationId] }); queryClient.invalidateQueries({ queryKey: ['preparations'] }); queryClient.invalidateQueries({ queryKey: ['preparation-validation', preparationId] }) }
  const add = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/requirements`, { method: 'POST', body: JSON.stringify({ title, is_required: true }) }), onSuccess: () => { setTitle(''); refresh() } })
  const confirmSource = useMutation({ mutationFn: checked => api(`/api/preparations/${preparationId}`, { method: 'PUT', body: JSON.stringify({ source_confirmed: checked }) }), onSuccess: refresh })
  const extract = useMutation({ mutationFn: () => Promise.all([api(`/api/preparations/${preparationId}/extract`, { method: 'POST' }), api(`/api/preparations/${preparationId}/form-fields/extract`, { method: 'POST' })]), onSuccess: refresh })
  const remove = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}`, { method: 'DELETE' }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['preparations'] }); onBack() } })
  const addField = useMutation({ mutationFn: () => api(`/api/preparations/${preparationId}/form-fields`, { method: 'POST', body: JSON.stringify({ label: fieldLabel, autofill_profile_key: autofillKey || null, autofill_consent: consent }) }), onSuccess: () => { setFieldLabel(''); setAutofillKey(''); setConsent(false); refresh() } })
  if (isLoading) return <p className="loading">신청 준비 내용을 불러오고 있어요…</p>
  if (error) return <div className="empty"><h1>신청 준비 건을 불러오지 못했습니다.</h1><p>{error.message}</p><button className="link-button" onClick={onBack}>준비함으로 돌아가기 →</button></div>
  const percent = data.total_count ? Math.round(data.completed_count / data.total_count * 100) : 0
  return <section className="preparation-detail"><button className="back-button" onClick={onBack}>← 신청 준비함</button><header><div><span className="eyebrow">APPLICATION CHECKLIST</span><h1>{data.current_policy_title}</h1><p>{data.organization || '담당 기관 확인 필요'} · 최종 확인 {data.policy_verified_at ? new Date(data.policy_verified_at).toLocaleDateString('ko-KR') : '확인 필요'}</p></div><div className="progress-circle"><strong>{percent}%</strong><span>준비 완료</span></div></header><section className="source-confirm"><div><h2>공식 원문을 먼저 확인해 주세요</h2><p>공고문과 첨부파일에서 서류 후보와 신청 문항을 찾아볼 수 있습니다. 자동 결과는 제출 전 반드시 원문과 대조해야 합니다.</p><div><a href={data.original_link_snapshot} target="_blank" rel="noreferrer">공식 공고 열기 ↗</a><button className="button outline small" onClick={() => extract.mutate()} disabled={extract.isPending}>{extract.isPending ? '공고 내용 찾는 중…' : '공고에서 서류·문항 찾기'}</button></div></div><label><input type="checkbox" checked={data.source_confirmed} onChange={event => confirmSource.mutate(event.target.checked)} /> 공식 원문을 확인했습니다</label></section><PreparationQualityPanel preparationId={preparationId} /><div className="requirement-list">{data.requirements.map(item => <RequirementItem key={item.id} preparationId={preparationId} item={item} />)}</div><section className="form-editor"><h2>신청서 문항 작성</h2><p>공식 양식에서 확인한 문항을 추가하세요. 프로필 값은 문항별 동의 시에만 복사됩니다.</p>{data.form_fields.map(item => <FormFieldItem key={item.id} preparationId={preparationId} item={item} />)}<form className="add-requirement" onSubmit={event => { event.preventDefault(); if (fieldLabel.trim()) addField.mutate() }}><input value={fieldLabel} onChange={event => setFieldLabel(event.target.value)} placeholder="예: 성명 또는 지원동기" /><select value={autofillKey} onChange={event => setAutofillKey(event.target.value)}>{autofillOptions.map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select>{autofillKey && <label><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /> 이 값 사용에 동의</label>}<button className="button dark" disabled={!fieldLabel.trim() || (autofillKey && !consent)}>문항 추가</button></form></section><form className="add-requirement" onSubmit={event => { event.preventDefault(); if (title.trim()) add.mutate() }}><div><strong>공고에서 확인한 서류 추가</strong><span>자동 추출되지 않은 증빙서류를 직접 추가하세요.</span></div><input value={title} onChange={event => setTitle(event.target.value)} placeholder="예: 주민등록초본" /><button className="button dark" disabled={!title.trim()}>항목 추가</button></form><div className="preparation-footer"><p>공식 제출은 완료되지 않습니다.</p><a className="button dark" href={`${API}/api/preparations/${preparationId}/export/hwpx`}>HWPX로 내보내기</a><button className="remove-button" onClick={() => { if (window.confirm('이 신청 준비 건과 체크리스트를 삭제할까요?')) remove.mutate() }}>신청 준비 건 삭제</button></div></section>
}

function Search({ onSelect, initialQuery = '' }) {
  const emptyFilters = { q: initialQuery, category: '', region: '', recruitment: 'open', age: '', employment_status: '', income_band: '', education_level: '' }
  const [draft, setDraft] = useState(emptyFilters)
  const [filters, setFilters] = useState(emptyFilters)
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== '').map(([key, value]) => [key, String(value)]))
  const { data = { items: [], total: 0 }, isLoading, error } = useQuery({ queryKey: ['search', filters], queryFn: () => api(`/api/policies?${params}`) })
  const update = event => setDraft(current => ({ ...current, [event.target.name]: event.target.value }))
  const reset = () => { setDraft(emptyFilters); setFilters(emptyFilters) }
  const select = (name, label, values) => <label><span>{label}</span><select name={name} value={draft[name]} onChange={update}><option value="">전체</option>{values.map(value => <option key={value}>{value}</option>)}</select></label>
  return <section><div className="list-heading search-heading"><span className="eyebrow">POLICY FINDER</span><h1>청년정책 찾기</h1><p>목포 거주자가 신청할 수 있는 목포·전남·전국 정책을 한 번에 찾아보세요.</p></div><form className="search-panel extended" onSubmit={event => { event.preventDefault(); setFilters({ ...draft }) }}><label className="search-keyword"><span>검색어</span><input name="q" value={draft.q} onChange={update} placeholder="정책명, 지원 내용, 기관 검색" /></label>{select('category', '분야', tags)}{select('region', '대상 지역', regions)}{select('employment_status', '취업 상태', profileOptions.employment_status)}{select('income_band', '소득 조건', profileOptions.income_band)}{select('education_level', '학력 조건', profileOptions.education_level)}<label><span>모집 상태</span><select name="recruitment" value={draft.recruitment} onChange={update}><option value="open">신청 가능</option><option value="closed">마감</option><option value="all">전체</option></select></label><label><span>나이</span><input name="age" type="number" min="0" max="120" value={draft.age} onChange={update} placeholder="예: 25" /></label><div className="search-buttons"><button className="button dark" type="submit">조건으로 검색</button><button className="button ghost" type="button" onClick={reset}>초기화</button></div></form>{error && <p className="error-box">{error.message}</p>}{isLoading ? <p className="loading">정책을 찾고 있어요…</p> : <><div className="result-summary"><strong>{data.total}</strong>개의 정책을 찾았습니다.</div>{data.items.length ? <div className="policy-list">{data.items.map(item => <PolicyCard key={item.id} item={item} onSelect={onSelect} />)}</div> : <div className="empty compact"><span className="eyebrow">NO RESULTS</span><h1>조건에 맞는 정책이 없어요.</h1><p>검색어나 필터를 조금 완화해서 다시 찾아보세요.</p><button className="link-button" onClick={reset}>검색 조건 초기화 →</button></div>}</>}</section>
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

function Detail({ policyId, onBack, user, onProfile, onPreparation }) {
  const { data: policy, isLoading, error } = useQuery({ queryKey: ['policy-detail', policyId], queryFn: () => api(`/api/policies/${policyId}`) })
  if (isLoading) return <p className="loading">정책 원문을 정리하고 있어요…</p>
  if (error) return <div className="empty"><h1>정책을 불러오지 못했습니다.</h1><p>{error.message}</p><button className="link-button" onClick={onBack}>검색으로 돌아가기 →</button></div>
  const age = policy.min_age || policy.max_age ? `${policy.min_age ?? '제한 없음'}세 ~ ${policy.max_age ?? '제한 없음'}세` : '원문 확인 필요'
  const attachments = String(policy.attachment_links || '').split(/[\n,]+/).map(value => value.trim()).filter(value => /^https?:\/\//i.test(value))
  return <section className="detail-page"><button className="back-button" onClick={onBack}>← 검색 결과로</button><div className="detail-hero"><span className="eyebrow">{policy.category} · {policy.target_region}</span><h1>{policy.title}</h1><p>{policy.organization || policy.source_site}</p><div className="detail-actions"><a className="button dark" href={policy.original_link} target="_blank" rel="noreferrer">공식 공고 확인 ↗</a>{user && <><PreparationButton policyId={policyId} onOpen={onPreparation} /><WishlistButton policyId={policyId} /></>}</div></div><Eligibility policyId={policyId} user={user} onProfile={onProfile} /><div className="detail-grid"><aside><dl><div><dt>신청 기간</dt><dd>{policy.period || [policy.application_start_date, policy.application_end_date].filter(Boolean).join(' ~ ') || '원문 확인 필요'}</dd></div><div><dt>연령 조건</dt><dd>{age}</dd></div><div><dt>거주 조건</dt><dd>{policy.residency_condition || policy.target_condition || policy.target_region}</dd></div><div><dt>담당 기관</dt><dd>{policy.organization || '원문 확인 필요'}</dd></div><div><dt>최종 확인</dt><dd>{policy.last_verified_at ? new Date(policy.last_verified_at).toLocaleDateString('ko-KR') : '확인 정보 없음'}</dd></div></dl></aside><article className="detail-content"><section><h2>지원 내용</h2><p>{policy.content || '상세 지원 내용은 공식 공고에서 확인해 주세요.'}</p></section><section><h2>신청 자격</h2><p>{policy.qualification_text || policy.target_condition || '구체적인 자격 조건은 공식 공고에서 확인해 주세요.'}</p></section><section><h2>신청 방법</h2><p>{policy.application_method || '신청 방법은 공식 공고에서 확인해 주세요.'}</p></section>{(attachments.length > 0 || policy.attachment_status) && <section><h2>첨부·제출 자료</h2><p>{policy.attachment_status || '첨부 공고문은 아래 공식 링크에서 확인할 수 있습니다.'}</p>{attachments.length > 0 && <ul>{attachments.map((url, index) => <li key={url}><a href={url} target="_blank" rel="noreferrer">첨부자료 {index + 1} 열기 ↗</a></li>)}</ul>}</section>}<div className="official-note"><strong>신청 전 반드시 확인하세요</strong><p>이 정보는 참고용입니다. 최종 자격과 일정은 담당 기관의 공식 공고를 기준으로 확인해 주세요.</p></div></article></div></section>
}

function List({ kind, onBack, onSelect }) {
  const endpoint = kind === 'policy' ? '/api/policies/recommended' : '/api/notifications'; const { data: items = [], isLoading } = useQuery({ queryKey: [kind], queryFn: () => api(endpoint) })
  const title = kind === 'policy' ? '내게 맞는 정책' : '새 알림'; const description = kind === 'policy' ? '지금 신청 가능한 정책만 모았습니다.' : '나의 조건과 관심사에 맞춘 신규·변경 공고입니다.'
  if (isLoading) return <p className="loading">정보를 살펴보고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">{kind === 'policy' ? 'PERSONAL MATCH' : 'POLICY UPDATES'}</span><h1>{title}</h1><p>{description}</p></div>{items.length ? <div className="policy-list">{items.map((item, index) => <PolicyCard item={item} notification={kind !== 'policy'} onSelect={kind === 'policy' ? onSelect : undefined} key={`${item.id || item.title}-${index}`} />)}</div> : <div className="empty"><span className="eyebrow">ALL CAUGHT UP</span><h1>{kind === 'policy' ? '지금은 새 공고를 기다리고 있어요.' : '확인할 새 알림이 없어요.'}</h1><p>매일 자동 수집을 통해 새 정책을 발견하는 즉시 이곳에 보여드릴게요.</p><button className="link-button" onClick={onBack}>대시보드로 돌아가기 →</button></div>}</section>
}

export default function App() {
  const [view, setView] = useState('home'); const [policyId, setPolicyId] = useState(null); const [preparationId, setPreparationId] = useState(null); const [searchQuery, setSearchQuery] = useState(''); const queryClient = useQueryClient(); const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: () => api('/api/me') })
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }); queryClient.setQueryData(['me'], null); setView('home') }
  const openDetail = id => { setPolicyId(id); setView('detail'); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  const openPreparation = id => { setPreparationId(id); setView('preparation-detail'); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  const openSearch = query => { setSearchQuery(query); setView('search'); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  if (isLoading) return <main className="shell"><p className="loading">서비스를 준비하고 있어요…</p></main>
  let content
  if (view === 'search') content = <Search onSelect={openDetail} initialQuery={searchQuery} />
  else if (view === 'chat') content = <Chat onSelect={openDetail} user={user} />
  else if (view === 'detail') content = <Detail policyId={policyId} user={user} onBack={() => setView('search')} onProfile={() => setView('profile')} onPreparation={openPreparation} />
  else if (view === 'profile' && user) content = <Profile user={user} onDone={() => setView('home')} />
  else if (view === 'policy') content = <List kind="policy" onBack={() => setView('home')} onSelect={openDetail} />
  else if (view === 'wishlist') content = <Wishlist onSelect={openDetail} />
  else if (view === 'preparations') content = <Preparations onOpen={openPreparation} onSelect={openDetail} />
  else if (view === 'preparation-detail') content = <PreparationDetail preparationId={preparationId} onBack={() => setView('preparations')} />
  else if (view === 'notification') content = <Notifications onSelect={openDetail} />
  else if (view === 'admin' && user.is_admin) content = <Admin />
  else content = <Home user={user} setView={setView} onSelect={openDetail} onSearch={openSearch} />
  return <div className="shell"><header><button className="brand" onClick={() => setView('home')}><span>M</span>목포 청년 정책</button><nav><button onClick={() => openSearch('')}>정책 검색</button><button onClick={() => setView('chat')}>AI 상담</button>{user && <><button onClick={() => setView('policy')}>맞춤 정책</button><button onClick={() => setView('wishlist')}>관심 정책</button><button onClick={() => setView('preparations')}>신청 준비함</button><button onClick={() => setView('notification')}>새 알림</button>{user.is_admin && <button onClick={() => setView('admin')}>관리자</button>}<button onClick={logout}>로그아웃</button></>}</nav></header><main>{content}</main><footer><strong>목포 청년 정책</strong><span>목포 거주 청년을 위한 맞춤 정책 탐색·알림 서비스</span><small>최종 신청 조건과 일정은 반드시 공식 공고를 확인해 주세요.</small></footer></div>
}
