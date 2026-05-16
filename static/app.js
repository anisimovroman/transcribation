'use strict'

let currentVideos = []
let selectedIds = new Set()
let sseSource = null
let libPage = 1

// ─── Runtime settings (synced with server) ───────────────────────
let _currentMode = 'balanced'
let _pendingMode = null
let _customPath = ''      // empty = use server default

// ─── Navigation ──────────────────────────────────────────────────

function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'))
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'))
  document.getElementById('s-' + name).classList.remove('hidden')
  event.target.classList.add('active')
  if (name === 'library') loadLibrary()
  if (name === 'settings') loadSettings()
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', ['channel', 'search'][i] === tab))
  document.getElementById('channel-form').classList.toggle('hidden', tab !== 'channel')
  document.getElementById('search-form').classList.toggle('hidden', tab !== 'search')
  document.getElementById('results').classList.add('hidden')
  document.getElementById('progress-section').classList.add('hidden')
}

// ─── Fetch videos ────────────────────────────────────────────────

async function submitChannel(e) {
  e.preventDefault()
  const url = document.getElementById('channel-url').value.trim()
  const limit = +document.getElementById('channel-limit').value
  const minDur = +document.getElementById('channel-min-dur').value * 60
  const excludeShorts = document.getElementById('channel-exclude-shorts').checked
  showToast('Загружаю список видео...')
  await fetchVideos('/api/channel', { channel_url: url, limit, min_duration_sec: minDur, exclude_shorts: excludeShorts })
}

async function submitSearch(e) {
  e.preventDefault()
  const query = document.getElementById('search-query').value.trim()
  const order = document.getElementById('search-order').value
  const date_filter = document.getElementById('search-date').value || null
  const limit = +document.getElementById('search-limit').value
  showToast('Ищу видео...')
  await fetchVideos('/api/search', { query, order, date_filter, limit })
}

async function fetchVideos(url, body) {
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!r.ok) { const err = await r.json(); showToast('Ошибка: ' + fmtErr(err, r.status), true); return }
    const data = await r.json()
    currentVideos = data.videos || []
    selectedIds = new Set(currentVideos.map(v => v.video_id))
    _estimateFromServer = null
    renderVideoList()
    showToast(`Найдено ${currentVideos.length} видео`)
    fetchEstimateFromServer(currentVideos)  // background cache check
  } catch (err) {
    showToast('Сетевая ошибка: ' + err.message, true)
  }
}

function renderVideoList() {
  const list = document.getElementById('video-list')
  document.getElementById('results-count').textContent = `${currentVideos.length} видео`
  updateSelectedCount()
  list.innerHTML = currentVideos.map(v => {
    return `<div class="video-item selected" id="vi-${v.video_id}" onclick="toggleVideo('${v.video_id}')">
      <input type="checkbox" checked id="cb-${v.video_id}" onclick="event.stopPropagation();toggleVideo('${v.video_id}')">
      <div class="video-meta">
        <div class="video-title">${escHtml(v.title)}</div>
        <div class="video-info">
          <span>${escHtml(v.channel || '')}</span>
          <span>${formatDuration(v.duration)}</span>
          ${v.view_count ? `<span>${formatViews(v.view_count)} просм.</span>` : ''}
          ${v.upload_date ? `<span>${formatDate(v.upload_date)}</span>` : ''}
        </div>
      </div>
    </div>`
  }).join('')
  document.getElementById('results').classList.remove('hidden')
}

function toggleVideo(id) {
  const item = document.getElementById('vi-' + id)
  const cb = document.getElementById('cb-' + id)
  if (selectedIds.has(id)) { selectedIds.delete(id); item.classList.remove('selected'); cb.checked = false }
  else { selectedIds.add(id); item.classList.add('selected'); cb.checked = true }
  updateSelectedCount()
}

function selectAll(val) {
  currentVideos.forEach(v => {
    const item = document.getElementById('vi-' + v.video_id)
    const cb = document.getElementById('cb-' + v.video_id)
    if (val) { selectedIds.add(v.video_id); item.classList.add('selected'); cb.checked = true }
    else { selectedIds.delete(v.video_id); item.classList.remove('selected'); cb.checked = false }
  })
  updateSelectedCount()
}

function updateSelectedCount() {
  document.getElementById('selected-count').textContent = selectedIds.size + ' выбрано'
  computeEstimate()
}

// ─── Time estimation ─────────────────────────────────────────────

function fmtTime(sec) {
  if (sec < 60) return Math.round(sec) + ' сек'
  if (sec < 3600) return Math.round(sec / 60) + ' мин'
  const h = Math.floor(sec / 3600)
  const m = Math.round((sec % 3600) / 60)
  return m > 0 ? `${h} ч ${m} мин` : `${h} ч`
}

let _estimateFromServer = null  // cache check result from backend

function computeEstimate() {
  const bar = document.getElementById('estimate-bar')
  const selected = currentVideos.filter(v => selectedIds.has(v.video_id))
  if (!selected.length) { bar.classList.add('hidden'); return }

  const CAPTION_RATE = 0.65
  const CAPTION_SEC = 3
  const WHISPER_RATIO = 0.5
  const WORKERS = 2

  // If we have server-side cache info, subtract cached videos
  let cachedCount = 0
  let cachedIds = new Set()
  if (_estimateFromServer) {
    cachedCount = _estimateFromServer.cached_count || 0
    // Use server counts for caption/whisper split if available
  }

  const n = selected.length
  const totalDurSec = selected.reduce((s, v) => s + (v.duration || 1800), 0)
  const avgDurSec = n ? totalDurSec / n : 1800

  const nCaption = Math.round(n * CAPTION_RATE)
  const nWhisper = n - nCaption

  const captionTimeSec = nCaption * CAPTION_SEC
  const whisperTimeSec = nWhisper * avgDurSec * WHISPER_RATIO
  const expectedSec = (captionTimeSec + whisperTimeSec) / WORKERS

  const bestSec = (n * CAPTION_SEC) / WORKERS
  const worstSec = (totalDurSec * WHISPER_RATIO) / WORKERS

  document.getElementById('est-main').textContent =
    `~${fmtTime(expectedSec)}  (от ${fmtTime(bestSec)} до ${fmtTime(worstSec)})`

  const cachedNote = cachedCount > 0 ? `  ·  ${cachedCount} уже в кэше` : ''
  document.getElementById('est-sub').textContent =
    `${n} видео: ~${nCaption} субтитры (~${fmtTime(captionTimeSec)}) + ~${nWhisper} Whisper (~${fmtTime(whisperTimeSec)})${cachedNote}`

  bar.classList.remove('hidden')
}

async function fetchEstimateFromServer(videos) {
  try {
    const body = { videos: videos.map(v => ({ video_id: v.video_id, duration_sec: v.duration || 1800 })) }
    const r = await fetch('/api/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!r.ok) return
    _estimateFromServer = await r.json()
    computeEstimate()
  } catch { /* non-critical */ }
}

// ─── Transcription ───────────────────────────────────────────────

async function startTranscription() {
  if (!selectedIds.size) { showToast('Выберите хотя бы одно видео', true); return }
  const selectedVideos = currentVideos
    .filter(v => selectedIds.has(v.video_id))
    .map(v => ({
      video_id: v.video_id,
      title: v.title || '',
      channel: v.channel || '',
      duration_sec: v.duration || 0,
      view_count: v.view_count || 0,
      upload_date: v.upload_date || '',
    }))
  const body = { videos: selectedVideos }
  if (_customPath) body.out_dir = _customPath
  try {
    const r = await fetch('/api/transcribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) { const err = await r.json(); showToast('Ошибка: ' + fmtErr(err, r.status), true); return }
    const data = await r.json()
    startSSE(data.job_id, selectedVideos.length)
    document.getElementById('progress-section').classList.remove('hidden')
    showToast('Транскрибация запущена')
  } catch (err) {
    showToast('Ошибка: ' + err.message, true)
  }
}

function startSSE(jobId, total) {
  if (sseSource) sseSource.close()
  document.getElementById('progress-count').textContent = `0/${total}`
  document.getElementById('progress-fill').style.width = '0%'
  sseSource = new EventSource('/api/progress/' + jobId)
  sseSource.onmessage = (e) => {
    const data = JSON.parse(e.data)
    const done = (data.completed || 0) + (data.failed || 0)
    const pct = data.total ? Math.round(done / data.total * 100) : 0
    document.getElementById('progress-fill').style.width = pct + '%'
    document.getElementById('progress-fill').style.background = pct === 100 ? 'var(--done)' : 'var(--accent)'
    document.getElementById('progress-count').textContent = `${done}/${data.total || total}`
    if (data.videos) {
      document.getElementById('job-list').innerHTML = data.videos.map(v => {
        const cls = { completed: 'v-done', failed: 'v-failed', processing: 'v-processing' }[v.status] || 'v-pending'
        const icon = { completed: '✓', failed: '✕', processing: '⟳' }[v.status] || '·'
        const orig = currentVideos.find(x => x.video_id === v.video_id)
        const label = v.title || (orig ? orig.title : v.video_id)
        const errNote = v.error_msg ? `<div class="video-error">${escHtml(v.error_msg)}</div>` : ''
        return `<div class="video-item">
          <div class="video-meta">
            <div class="video-title">${escHtml(label)}</div>
            ${errNote}
          </div>
          <span class="video-status ${cls}">${icon} ${v.status}</span>
        </div>`
      }).join('')
    }
    if (data.status === 'completed') {
      sseSource.close()
      showToast(`✓ Готово! ${data.completed} транскрипций сохранено`)
    }
  }
  sseSource.onerror = () => sseSource.close()
}

// ─── Library ─────────────────────────────────────────────────────

let libSearchTimer = null
function searchLibrary() {
  clearTimeout(libSearchTimer)
  libSearchTimer = setTimeout(() => { libPage = 1; loadLibrary() }, 300)
}

async function loadLibrary(page = libPage) {
  libPage = page
  const q = document.getElementById('lib-search')?.value?.trim()
  const url = `/api/results?page=${page}&per_page=20${q ? '&q=' + encodeURIComponent(q) : ''}`
  const r = await fetch(url)
  const data = await r.json()
  document.getElementById('lib-total').textContent = `${data.total} транскрипций`
  document.getElementById('lib-list').innerHTML = data.results.map(item => `
    <div class="lib-item">
      <div class="lib-meta">
        <div class="lib-title">${escHtml(item.title || item.video_id)}</div>
        <div class="lib-info">
          <span>${escHtml(item.channel || '')}</span>
          <span>${item.duration_sec ? formatDuration(item.duration_sec) : ''}</span>
          <span>${item.upload_date ? formatDate(item.upload_date) : ''}</span>
          <span class="badge-method">${item.method || ''}</span>
        </div>
      </div>
      <div class="lib-actions">
        <a href="https://youtube.com/watch?v=${item.video_id}" target="_blank" class="btn-link">YT ↗</a>
      </div>
    </div>
  `).join('') || '<div style="padding:20px;text-align:center;color:var(--muted)">Нет транскрипций</div>'

  // pagination
  const totalPages = Math.ceil(data.total / 20)
  document.getElementById('lib-pagination').innerHTML = totalPages > 1
    ? Array.from({ length: totalPages }, (_, i) => i + 1)
        .map(p => `<button class="page-btn${p === page ? ' active' : ''}" onclick="loadLibrary(${p})">${p}</button>`)
        .join('')
    : ''
}

// ─── Export ──────────────────────────────────────────────────────

async function exportObsidian() {
  const vaultPath = document.getElementById('obsidian-path').value.trim()
  if (!vaultPath) { showToast('Укажите путь к vault', true); return }
  const r = await fetch('/api/export/obsidian', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_ids: await getAllCachedIds(), vault_path: vaultPath }),
  })
  const data = await r.json()
  const el = document.getElementById('obsidian-result')
  el.classList.remove('hidden')
  el.textContent = `Экспортировано: ${data.exported}\nПропущено: ${data.skipped?.length || 0}\n${data.paths?.slice(0, 3).join('\n') || ''}`
  showToast(`✓ ${data.exported} файлов экспортировано`)
}

async function downloadZip() {
  const r = await fetch('/api/export/zip', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_ids: [] }),
  })
  if (!r.ok) { showToast('Ошибка при создании ZIP', true); return }
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = 'transcripts.zip'; a.click()
  URL.revokeObjectURL(url)
  showToast('ZIP скачивается...')
}

async function getAllCachedIds() {
  const r = await fetch('/api/results?per_page=200')
  const d = await r.json()
  return d.results.map(x => x.video_id)
}

// ─── Helpers ─────────────────────────────────────────────────────

function fmtErr(err, status) {
  const d = err && err.detail
  if (!d) return String(status)
  if (Array.isArray(d)) return d.map(e => e.msg || JSON.stringify(e)).join('; ')
  if (typeof d === 'string') return d
  return JSON.stringify(d)
}

function escHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function formatDuration(sec) {
  if (!sec) return ''
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

function formatViews(n) {
  return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(0) + 'K' : String(n)
}

function formatDate(d) {
  if (!d || d.length < 8) return d
  if (d.length === 8 && /^\d+$/.test(d)) return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`
  return d.slice(0, 10)
}

let toastTimer = null
function showToast(msg, isError = false) {
  const el = document.getElementById('toast')
  el.textContent = msg
  el.style.borderColor = isError ? 'rgba(239,68,68,.4)' : 'var(--border)'
  el.style.color = isError ? 'var(--error)' : 'var(--primary)'
  el.classList.add('show')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => el.classList.remove('show'), 2500)
}

// ─── Settings ─────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const r = await fetch('/api/settings')
    const data = await r.json()
    _currentMode = data.mode || 'balanced'
    _customPath = (data.transcripts_dir !== data.default_transcripts_dir) ? data.transcripts_dir : ''

    // Restore path input
    const pathInput = document.getElementById('transcripts-path')
    if (_customPath) {
      pathInput.value = _customPath
      setPathStatus('ok', '✓ ' + _customPath)
    } else {
      pathInput.value = ''
      setPathStatus('', 'По умолчанию: ' + data.default_transcripts_dir)
    }

    renderModeCards(_currentMode)
    setModeStatus(_currentMode)
  } catch { /* non-critical */ }
}

function renderModeCards(active) {
  document.querySelectorAll('.mode-card').forEach(card => {
    card.classList.toggle('active', card.dataset.mode === active)
  })
}

function setModeStatus(mode) {
  const labels = { safe: 'Экономный — 1 поток', balanced: 'Стандартный — 2 потока', fast: 'Быстрый — 4 потока' }
  const el = document.getElementById('mode-status')
  if (el) el.textContent = 'Текущий режим: ' + (labels[mode] || mode)
}

function setPathStatus(type, msg) {
  const el = document.getElementById('path-status')
  if (!el) return
  el.textContent = msg
  el.className = 'path-status' + (type ? ' ' + type : '')
}

async function pickFolder(inputId, statusId, title) {
  const btn = event.currentTarget
  const orig = btn.textContent
  btn.textContent = '⏳ Открываю...'
  btn.disabled = true
  try {
    const r = await fetch('/api/pick-folder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    if (r.status === 204 || r.status === 400) { return }  // cancelled
    if (!r.ok) { const e = await r.json(); showToast(fmtErr(e, r.status), true); return }
    const data = await r.json()
    document.getElementById(inputId).value = data.path
    if (statusId) setPathStatus('ok', '✓ ' + data.path)

    // Auto-save if it's the transcripts path
    if (inputId === 'transcripts-path') {
      await fetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcripts_dir: data.path }),
      })
      _customPath = data.path
      showToast('Папка сохранена')
    }
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
  } finally {
    btn.textContent = orig
    btn.disabled = false
  }
}

async function validatePath() {
  const raw = document.getElementById('transcripts-path').value.trim()
  if (!raw) { setPathStatus('err', 'Введи путь'); return }
  setPathStatus('', 'Проверяю...')
  try {
    const r = await fetch('/api/validate-path', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: raw }),
    })
    const data = await r.json()
    if (!r.ok) { setPathStatus('err', '✗ ' + fmtErr(data, r.status)); return }

    // Save to server
    const r2 = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcripts_dir: data.resolved }),
    })
    if (!r2.ok) { setPathStatus('err', '✗ Не удалось сохранить'); return }
    _customPath = data.resolved
    document.getElementById('transcripts-path').value = data.resolved
    setPathStatus('ok', '✓ Сохранено: ' + data.resolved)
    showToast('Папка сохранена')
  } catch (e) {
    setPathStatus('err', '✗ ' + e.message)
  }
}

async function resetPath() {
  document.getElementById('transcripts-path').value = ''
  _customPath = ''
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcripts_dir: '' }),
  }).catch(() => {})
  // Reload to get default path
  loadSettings()
  showToast('Путь сброшен на стандартный')
}

const MODE_WARNINGS = {
  fast: 'Режим «Быстрый» запускает 4 параллельных потока транскрибации. Это может сильно нагрузить процессор и память — компьютер будет ощутимо тормозить во время работы.',
  balanced: null,
  safe: null,
}

function selectMode(mode) {
  if (mode === _currentMode) return
  const fromSafe = _currentMode === 'safe'
  const warning = MODE_WARNINGS[mode]

  if (warning || (fromSafe && mode !== 'safe')) {
    _pendingMode = mode
    const fromLabel = { safe: 'Экономный', balanced: 'Стандартный', fast: 'Быстрый' }[_currentMode]
    const toLabel   = { safe: 'Экономный', balanced: 'Стандартный', fast: 'Быстрый' }[mode]
    document.getElementById('modal-text').textContent =
      (warning || `Вы переключаетесь с режима «${fromLabel}» на «${toLabel}».`) +
      (fromSafe ? ' Ты уходишь с самого безопасного режима.' : '')
    document.getElementById('mode-modal').classList.remove('hidden')
    return
  }
  applyMode(mode)
}

function cancelMode() {
  _pendingMode = null
  document.getElementById('mode-modal').classList.add('hidden')
}

async function confirmMode() {
  document.getElementById('mode-modal').classList.add('hidden')
  if (_pendingMode) await applyMode(_pendingMode)
  _pendingMode = null
}

async function applyMode(mode) {
  try {
    const r = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    if (!r.ok) { showToast('Ошибка сохранения режима', true); return }
    _currentMode = mode
    renderModeCards(mode)
    setModeStatus(mode)
    const labels = { safe: 'Экономный', balanced: 'Стандартный', fast: 'Быстрый' }
    showToast('Режим: ' + labels[mode])
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
  }
}

// Init settings on page load
window.addEventListener('load', () => loadSettings())
