import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import './App.css'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'
const tags = ['취업', '창업', '주거', '교육', '복지', '문화']

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: 'include', headers: { 'Content-Type': 'application/json', ...options.headers }, ...options })
  if (response.status === 401) return null
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '요청을 처리하지 못했습니다.')
  return response.json()
}

function Landing() {
  return <><section className="hero"><div><span className="eyebrow">MOKPO YOUTH POLICY</span><h1>목포 청년의 오늘에<br /><em>딱 맞는 정책</em>을.</h1><p>흩어진 청년 지원 정보를 모으고, 나의 조건에 맞는 공고만 골라 알려드립니다.</p><button className="button kakao" onClick={() => { window.location.href = `${API}/auth/kakao` }}>● 카카오로 3초 만에 시작하기</button><small>로그인 후 관심 분야와 생년월일을 설정하면 맞춤 추천이 시작됩니다.</small></div><aside><span>오늘의 서비스</span><strong>정책 탐색부터<br />새 공고 알림까지</strong><div>{tags.slice(0, 4).map(tag => <b key={tag}>{tag}</b>)}</div></aside></section><section className="features">{[['01', '맞춤 추천', '연령, 목포 거주, 관심 분야를 바탕으로 정책을 추립니다.'], ['02', '변경 감지', '매일 수집해 새 공고와 수정된 내용을 찾아냅니다.'], ['03', '한눈에 확인', '지원 조건과 마감일, 원문 링크까지 한 화면에서 봅니다.']].map(([n, title, copy]) => <article key={n}><span>{n}</span><h2>{title}</h2><p>{copy}</p></article>)}</section></>
}

function Profile({ user, onDone }) {
  const queryClient = useQueryClient(); const [birthDate, setBirthDate] = useState(user.birth_date || ''); const [interests, setInterests] = useState(user.interests || [])
  const mutation = useMutation({ mutationFn: () => api('/api/me/profile', { method: 'PUT', body: JSON.stringify({ birth_date: birthDate, interests }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['me'] }); queryClient.invalidateQueries({ queryKey: ['notifications'] }); onDone() } })
  const toggle = tag => setInterests(items => items.includes(tag) ? items.filter(item => item !== tag) : [...items, tag])
  return <section className="profile-page"><div className="page-heading"><span className="eyebrow">PERSONALIZE</span><h1>나에게 맞게<br /><em>정책 추천을 설정</em>해요.</h1><p>입력한 정보는 맞춤 정책 추천과 알림 후보 생성에만 사용됩니다.</p></div><form className="profile-card" onSubmit={event => { event.preventDefault(); mutation.mutate() }}><label>생년월일<input required type="date" value={birthDate} onChange={event => setBirthDate(event.target.value)} /></label><div className="residence"><span>거주 지역</span><strong>목포시</strong><small>현재 서비스는 목포 거주 청년을 대상으로 합니다.</small></div><fieldset><legend>관심 분야 <small>복수 선택 가능</small></legend><div className="interests">{tags.map(tag => <label key={tag}><input type="checkbox" checked={interests.includes(tag)} onChange={() => toggle(tag)} /><span>{tag}</span></label>)}</div></fieldset>{mutation.error && <p className="error">{mutation.error.message}</p>}<button className="button dark" disabled={mutation.isPending}>저장하고 맞춤 정책 보기</button></form></section>
}

function List({ kind, onBack }) {
  const endpoint = kind === 'policy' ? '/api/policies/recommended' : '/api/notifications'; const { data: items = [], isLoading } = useQuery({ queryKey: [kind], queryFn: () => api(endpoint) })
  const title = kind === 'policy' ? '내게 맞는 정책' : '새 알림'; const description = kind === 'policy' ? '지금 신청 가능한 정책만 모았습니다.' : '나의 조건과 관심사에 맞춘 신규·변경 공고입니다.'
  if (isLoading) return <p className="loading">정보를 살펴보고 있어요…</p>
  return <section><div className="list-heading"><span className="eyebrow">{kind === 'policy' ? 'PERSONAL MATCH' : 'POLICY UPDATES'}</span><h1>{title}</h1><p>{description}</p></div>{items.length ? <div className="policy-list">{items.map((item, index) => <article className="policy-card" key={`${item.title}-${index}`}>{kind === 'policy' ? <div><span>{item.category}</span><span>마감 {item.application_end_date || '상시 또는 별도 확인'}</span></div> : <b className="notice">{item.change_type === 'new' ? '새 공고' : '변경 공고'}</b>}<h2>{item.title}</h2><p>{kind === 'policy' ? item.content?.slice(0, 250) : item.match_reason}</p><a href={item.original_link} target="_blank" rel="noreferrer">공고 원문 보기 ↗</a></article>)}</div> : <div className="empty"><span className="eyebrow">ALL CAUGHT UP</span><h1>{kind === 'policy' ? '지금은 새 공고를 기다리고 있어요.' : '확인할 새 알림이 없어요.'}</h1><p>매일 자동 수집을 통해 새 정책을 발견하는 즉시 이곳에 보여드릴게요.</p><button className="link-button" onClick={onBack}>대시보드로 돌아가기 →</button></div>}</section>
}

function Dashboard({ user, setView }) {
  const { data: policies = [] } = useQuery({ queryKey: ['policy'], queryFn: () => api('/api/policies/recommended') }); const { data: notices = [] } = useQuery({ queryKey: ['notification'], queryFn: () => api('/api/notifications') })
  return <><section className="dashboard-head"><div><span className="eyebrow">MY POLICY DESK</span><h1>반가워요, <em>{user.display_name}</em>님</h1><p>관심 분야 <strong>{user.interests?.join(', ') || '설정 필요'}</strong>을 기준으로 정책을 살피고 있어요.</p></div><button className="link-button" onClick={() => setView('profile')}>프로필 수정 →</button></section><section className="stats"><button onClick={() => setView('policy')}><span>나에게 맞는 정책</span><strong>{policies.length}<small>건</small></strong><p>신청 가능한 공고 보기 →</p></button><button className="accent" onClick={() => setView('notification')}><span>새 알림</span><strong>{notices.length}<small>건</small></strong><p>신규·변경 공고 확인 →</p></button></section><section className="guide"><div><span className="eyebrow">NEXT STEP</span><h2>정책 정보는 매일 새로워집니다.</h2><p>자동 수집기가 새 공고를 발견하면 관심 분야와 조건을 비교해 이곳에 알려드려요.</p></div><button className="button dark" onClick={() => setView('policy')}>맞춤 정책 보러가기</button></section></>
}

export default function App() {
  const [view, setView] = useState('home'); const queryClient = useQueryClient(); const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: () => api('/api/me') })
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }); queryClient.setQueryData(['me'], null); setView('home') }
  if (isLoading) return <main className="shell"><p className="loading">서비스를 준비하고 있어요…</p></main>
  const content = !user ? <Landing /> : view === 'profile' ? <Profile user={user} onDone={() => setView('home')} /> : view === 'policy' ? <List kind="policy" onBack={() => setView('home')} /> : view === 'notification' ? <List kind="notification" onBack={() => setView('home')} /> : <Dashboard user={user} setView={setView} />
  return <div className="shell"><header><button className="brand" onClick={() => setView('home')}><span>M</span>목포 청년 정책</button>{user && <nav><button onClick={() => setView('policy')}>맞춤 정책</button><button onClick={() => setView('notification')}>새 알림</button><button onClick={logout}>로그아웃</button></nav>}</header><main>{content}</main><footer>목포 거주 청년을 위한 맞춤 정책 알림 서비스</footer></div>
}
