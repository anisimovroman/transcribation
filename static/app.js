'use strict'

let currentVideos = []
let selectedIds = new Set()
let sseSource = null
let libPage = 1
let _currentJobId = null

// ─── Runtime settings (synced with server) ───────────────────────
let _currentMode = 'balanced'
let _pendingMode = null
let _customPath = ''       // empty = use server default
let _saveMode = 'separate' // "separate" | "single"
let _singleFile = ''       // path when _saveMode === "single"

// ─── Navigation ──────────────────────────────────────────────────

function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'))
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'))
  document.getElementById('s-' + name).classList.remove('hidden')
  event.target.classList.add('active')
  if (name === 'library') { loadLibrary(); loadChannelFilter() }
  if (name === 'settings') loadSettings()
}

function switchTab(tab) {
  const tabs = ['channel', 'search', 'url']
  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', tabs[i] === tab))
  document.getElementById('channel-form').classList.toggle('hidden', tab !== 'channel')
  document.getElementById('search-form').classList.toggle('hidden', tab !== 'search')
  document.getElementById('url-form').classList.toggle('hidden', tab !== 'url')
  document.getElementById('results').classList.add('hidden')
  document.getElementById('progress-section').classList.add('hidden')
}

async function submitUrls(e) {
  e.preventDefault()
  const raw = document.getElementById('url-input').value.trim()
  if (!raw) { showToast('Введи хотя бы одну ссылку', true); return }
  // Split by newlines and commas, filter empty
  const urls = raw.split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
  if (!urls.length) { showToast('Не найдено ссылок', true); return }
  showToast(`Загружаю метаданные ${urls.length} видео...`)
  await fetchVideos('/api/videos', { urls })
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
    refreshQuota()
  } catch (err) {
    showToast('Сетевая ошибка: ' + err.message, true)
  }
}

async function refreshQuota() {
  try {
    const r = await fetch('/api/quota')
    if (!r.ok) return
    const d = await r.json()
    const bar = document.getElementById('quota-bar')
    if (!bar) return
    // Always show bar — even at 0 (tracking resets on server restart)
    bar.classList.remove('hidden')
    const pct = Math.min(100, Math.round(d.used / d.total * 100))
    document.getElementById('quota-fill').style.width = pct + '%'
    document.getElementById('quota-fill').style.background =
      pct >= 90 ? 'var(--error)' : pct >= 70 ? '#f59e0b' : 'var(--accent)'
    document.getElementById('quota-text').textContent = d.used === 0
      ? `0 / ${d.total.toLocaleString()} (новая сессия)`
      : `${d.used.toLocaleString()} / ${d.total.toLocaleString()} · осталось ${d.remaining.toLocaleString()}`
  } catch { /* non-critical */ }
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
    requestNotifyPermission()
    startSSE(data.job_id, selectedVideos.length)
    document.getElementById('progress-section').classList.remove('hidden')
    showToast('Транскрибация запущена')
  } catch (err) {
    showToast('Ошибка: ' + err.message, true)
  }
}

const _processingStart = {}  // videoId -> startMs when status first became 'processing'

function _setStopBtn(visible) {
  document.getElementById('stop-btn')?.classList.toggle('hidden', !visible)
}

function _setResumeBtn(visible) {
  document.getElementById('resume-btn')?.classList.toggle('hidden', !visible)
}

function _renderJobVideos(videos, jobId) {
  document.getElementById('job-list').innerHTML = videos.map(v => {
    const cls = { completed: 'v-done', failed: 'v-failed', processing: 'v-processing' }[v.status] || 'v-pending'
    const icon = { completed: '✓', failed: '✕', processing: '⟳' }[v.status] || '·'
    const orig = currentVideos.find(x => x.video_id === v.video_id)
    const label = v.title || (orig ? orig.title : v.video_id)
    const errNote = v.error_msg ? `<div class="video-error">${escHtml(v.error_msg)}</div>` : ''
    const retryBtn = v.status === 'failed' && v.error_msg !== 'Отменено'
      ? `<button class="btn-retry" onclick="retryVideo(${jobId},'${v.video_id}')">↺ Повторить</button>`
      : ''
    return `<div class="video-item" id="job-item-${v.video_id}">
      <div class="video-meta">
        <div class="video-title">${escHtml(label)}</div>
        ${errNote}
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        ${retryBtn}
        <span class="video-status ${cls}">${icon}</span>
      </div>
      ${buildVideoProgressBar(v)}
    </div>`
  }).join('')
}

function startSSE(jobId, total) {
  if (sseSource) sseSource.close()
  _currentJobId = jobId
  _setStopBtn(true)
  _setResumeBtn(false)
  if (total) document.getElementById('progress-count').textContent = `0/${total}`
  document.getElementById('progress-fill').style.width = '0%'
  sseSource = new EventSource('/api/progress/' + jobId)
  sseSource.onmessage = (e) => {
    const data = JSON.parse(e.data)
    const done = (data.completed || 0) + (data.failed || 0)
    const pct = data.total ? Math.round(done / data.total * 100) : 0
    document.getElementById('progress-fill').style.width = pct + '%'
    document.getElementById('progress-fill').style.background = pct === 100 ? 'var(--done)' : 'var(--accent)'
    document.getElementById('progress-count').textContent = `${done}/${data.total || total}`
    if (data.videos) _renderJobVideos(data.videos, jobId)
    if (data.status === 'completed') {
      sseSource.close()
      _setStopBtn(false)
      showToast(`✓ Готово! ${data.completed} транскрипций сохранено`)
      notifyJobDone(data.completed, data.failed || 0)
    } else if (data.status === 'cancelled') {
      sseSource.close()
      _setStopBtn(false)
      _setResumeBtn(false)
      showToast(`Остановлено · ${data.completed || 0} сохранено`)
    }
  }
  sseSource.onerror = () => { sseSource.close(); _setStopBtn(false) }
}

function continueTranscription() {
  if (!_currentJobId) return
  _setResumeBtn(false)
  startSSE(_currentJobId, 0)
}

async function stopTranscription() {
  if (!_currentJobId) {
    showToast('Нет активной задачи', true)
    return
  }
  const btn = document.getElementById('stop-btn')
  btn.disabled = true
  btn.textContent = '⏳ Останавливаю...'
  try {
    const r = await fetch(`/api/jobs/${_currentJobId}/cancel`, { method: 'POST' })
    const payload = await r.json().catch(() => ({}))
    if (!r.ok) {
      if (r.status === 400) {
        // Job already finished — just close SSE gracefully
        sseSource?.close()
        _setStopBtn(false)
        _currentJobId = null
        showToast('Задача уже завершилась')
      } else {
        showToast('Ошибка остановки: ' + (payload.detail || r.status), true)
        btn.disabled = false
        btn.textContent = '⏹ Стоп'
      }
      return
    }
    // Close SSE immediately — re-render DOM to stop CSS animations
    sseSource?.close()
    sseSource = null
    _setStopBtn(false)
    btn.disabled = false
    btn.textContent = '⏹ Стоп'
    // Re-render with server-provided video states to clear running CSS animations
    if (payload.videos) _renderJobVideos(payload.videos, _currentJobId)
    // Keep _currentJobId so "Продолжить" can reconnect SSE
    _setResumeBtn(true)
    const saved = payload.completed ?? 0
    showToast(saved > 0 ? `Остановлено · ${saved} сохранено` : 'Остановлено')
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
    btn.disabled = false
    btn.textContent = '⏹ Стоп'
  }
}

function buildVideoProgressBar(v) {
  const id = v.video_id
  if (v.status === 'processing') {
    if (!_processingStart[id]) _processingStart[id] = Date.now()
    const elapsed = (Date.now() - _processingStart[id]) / 1000
    // Estimate: Whisper ~0.5x realtime on CPU, ~6x on Apple GPU → use 0.4x as middle ground
    const estimatedSec = Math.max(8, (v.duration_sec || 180) * 0.4)
    return `<div class="vpbar">
      <div class="vpbar-fill running" style="animation-duration:${estimatedSec.toFixed(1)}s;animation-delay:-${elapsed.toFixed(1)}s"></div>
    </div>`
  }
  if (v.status === 'completed') {
    delete _processingStart[id]
    return `<div class="vpbar"><div class="vpbar-fill" style="width:100%;background:var(--done)"></div></div>`
  }
  if (v.status === 'failed') {
    delete _processingStart[id]
    return `<div class="vpbar"><div class="vpbar-fill" style="width:100%;background:var(--error);opacity:.5"></div></div>`
  }
  // pending
  return `<div class="vpbar"><div class="vpbar-fill" style="width:0"></div></div>`
}

async function retryVideo(jobId, videoId) {
  const btn = event.target
  btn.disabled = true
  btn.textContent = '⟳ ...'
  try {
    const r = await fetch(`/api/retry/${jobId}/${videoId}`, { method: 'POST' })
    if (!r.ok) {
      btn.disabled = false; btn.textContent = '↺ Повторить'
      showToast('Ошибка повтора', true); return
    }
    showToast('Повторяю...')
    if (!sseSource || sseSource.readyState === EventSource.CLOSED) {
      document.getElementById('progress-section').classList.remove('hidden')
      startSSE(jobId, 0)
    }
  } catch (e) {
    btn.disabled = false; btn.textContent = '↺ Повторить'
    showToast('Ошибка: ' + e.message, true)
  }
}

function requestNotifyPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission()
  }
}

function notifyJobDone(completed, failed) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  const body = failed > 0
    ? `✓ ${completed} транскрипций сохранено · ${failed} ошибок`
    : `✓ ${completed} транскрипций сохранено`
  new Notification('Транскрибация завершена', { body })
}

// ─── Library ─────────────────────────────────────────────────────

let libSearchTimer = null
const _transcriptCache = {}

function searchLibrary() {
  clearTimeout(libSearchTimer)
  libSearchTimer = setTimeout(() => { libPage = 1; loadLibrary() }, 300)
}

async function loadChannelFilter() {
  try {
    const r = await fetch('/api/channels')
    const data = await r.json()
    const sel = document.getElementById('filter-channel')
    if (!sel) return
    const current = sel.value
    sel.innerHTML = '<option value="">Все каналы</option>' +
      data.channels.map(c => `<option value="${escHtml(c)}"${c === current ? ' selected' : ''}>${escHtml(c)}</option>`).join('')
  } catch { /* non-critical */ }
}

function applyFilters() {
  libPage = 1
  loadLibrary()
  const hasFilters = ['filter-channel', 'filter-method', 'filter-date-from', 'filter-date-to']
    .some(id => document.getElementById(id)?.value)
  const resetBtn = document.getElementById('filters-reset')
  if (resetBtn) resetBtn.style.display = hasFilters ? '' : 'none'
}

function resetFilters() {
  ['filter-channel', 'filter-method', 'filter-date-from', 'filter-date-to'].forEach(id => {
    const el = document.getElementById(id)
    if (el) el.value = ''
  })
  document.getElementById('filters-reset').style.display = 'none'
  libPage = 1
  loadLibrary()
}

async function loadLibrary(page = libPage) {
  libPage = page
  const q = document.getElementById('lib-search')?.value?.trim()
  const channel = document.getElementById('filter-channel')?.value || ''
  const method = document.getElementById('filter-method')?.value || ''
  const dateFrom = document.getElementById('filter-date-from')?.value || ''
  const dateTo = document.getElementById('filter-date-to')?.value || ''
  const params = new URLSearchParams({ page, per_page: 20 })
  if (q) params.set('q', q)
  if (channel) params.set('channel', channel)
  if (method) params.set('method', method)
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  const url = `/api/results?${params}`
  const r = await fetch(url)
  const data = await r.json()
  document.getElementById('lib-total').textContent = `${data.total} транскрипций`
  document.getElementById('lib-list').innerHTML = data.results.map(item => {
    const id = item.video_id
    const methodBadge = item.method ? `<span class="badge-method">${item.method}</span>` : ''
    const snippet = item.snippet ? `<div class="lib-snippet">${item.snippet}</div>` : ''
    return `<div class="lib-item" id="lib-${id}">
      <div class="lib-item-row">
        <div class="lib-meta">
          <div class="lib-title">${escHtml(item.title || id)}</div>
          <div class="lib-info">
            <span>${escHtml(item.channel || '')}</span>
            <span>${item.duration_sec ? formatDuration(item.duration_sec) : ''}</span>
            <span>${item.upload_date ? formatDate(item.upload_date) : ''}</span>
            ${methodBadge}
          </div>
          ${snippet}
        </div>
        <div class="lib-actions">
          <button class="btn-link" onclick="copyTranscript('${id}')">📋</button>
          <button class="btn-link viewer-toggle" onclick="toggleViewer('${id}')">Открыть</button>
          <a href="https://youtube.com/watch?v=${id}" target="_blank" class="btn-link">YT ↗</a>
          <button class="btn-link btn-delete" onclick="deleteTranscript('${id}', event)" title="Удалить">✕</button>
        </div>
      </div>
      <div class="lib-viewer hidden" id="viewer-${id}">
        <div class="viewer-toolbar">
          <button class="btn-link" onclick="copyTranscript('${id}')">📋 Копировать</button>
          <a href="/api/transcripts/${id}/srt" class="btn-link" download>⬇ SRT</a>
        </div>
        <div class="viewer-text" id="viewer-text-${id}"></div>
      </div>
    </div>`
  }).join('') || '<div style="padding:20px;text-align:center;color:var(--muted)">Нет транскрипций</div>'

  const totalPages = Math.ceil(data.total / 20)
  document.getElementById('lib-pagination').innerHTML = totalPages > 1
    ? Array.from({ length: totalPages }, (_, i) => i + 1)
        .map(p => `<button class="page-btn${p === page ? ' active' : ''}" onclick="loadLibrary(${p})">${p}</button>`)
        .join('')
    : ''
}

async function fetchTranscript(videoId) {
  if (_transcriptCache[videoId]) return _transcriptCache[videoId]
  const r = await fetch(`/api/transcripts/${videoId}`)
  if (!r.ok) return null
  const data = await r.json()
  _transcriptCache[videoId] = data
  return data
}

function renderTranscriptText(text) {
  if (!text) return '<span style="color:var(--muted)">Текст недоступен</span>'
  const tsRe = /^\[(\d{2}:\d{2}:\d{2})\] /
  const lines = text.split('\n')
  const hasTs = lines.some(l => tsRe.test(l))
  if (hasTs) {
    return lines.map(line => {
      const m = line.match(/^(\[\d{2}:\d{2}:\d{2}\])\s*(.*)$/)
      if (m) return `<span class="viewer-ts">${escHtml(m[1])}</span> ${escHtml(m[2])}`
      return escHtml(line)
    }).join('\n')
  }
  return escHtml(text)
}

async function toggleViewer(videoId) {
  const viewer = document.getElementById('viewer-' + videoId)
  if (!viewer) return
  if (!viewer.classList.contains('hidden')) {
    viewer.classList.add('hidden')
    const btn = viewer.closest('.lib-item')?.querySelector('.viewer-toggle')
    if (btn) btn.textContent = 'Открыть'
    return
  }
  const textEl = document.getElementById('viewer-text-' + videoId)
  viewer.classList.remove('hidden')
  const btn = viewer.closest('.lib-item')?.querySelector('.viewer-toggle')
  if (btn) btn.textContent = 'Закрыть'
  if (textEl.dataset.loaded) return
  textEl.innerHTML = '<span style="color:var(--muted)">Загружаю...</span>'
  const data = await fetchTranscript(videoId)
  if (!data) { textEl.textContent = 'Ошибка загрузки'; return }
  textEl.innerHTML = renderTranscriptText(data.text)
  textEl.dataset.loaded = '1'
}

async function deleteTranscript(videoId, event) {
  event.stopPropagation()
  if (!confirm('Удалить эту транскрипцию? Файл .txt тоже будет удалён.')) return
  try {
    const r = await fetch(`/api/transcripts/${videoId}`, { method: 'DELETE' })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      showToast('Ошибка удаления: ' + (err.detail || r.status), true)
      return
    }
    delete _transcriptCache[videoId]
    document.getElementById('lib-' + videoId)?.remove()
    // If item not found by lib- id, reload the whole list
    if (!document.getElementById('lib-' + videoId)) loadLibrary()
    showToast('Удалено')
    loadChannelFilter()
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
  }
}

async function clearAllTranscripts() {
  const totalEl = document.getElementById('lib-total')
  const count = totalEl ? totalEl.textContent : ''
  if (!confirm(`Удалить ВСЕ транскрипции (${count})?\nФайлы .txt тоже будут удалены.\nЭто действие нельзя отменить.`)) return
  try {
    const r = await fetch('/api/transcripts/delete-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: [] }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      showToast('Ошибка очистки: ' + (err.detail || r.status), true)
      return
    }
    const data = await r.json()
    Object.keys(_transcriptCache).forEach(k => delete _transcriptCache[k])
    loadLibrary()
    loadChannelFilter()
    showToast(`✓ Удалено ${data.deleted} транскрипций`)
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
  }
}

async function copyTranscript(videoId) {
  try {
    const data = await fetchTranscript(videoId)
    if (!data?.text) { showToast('Текст не найден', true); return }
    await navigator.clipboard.writeText(data.text)
    showToast('✓ Скопировано!')
  } catch (e) {
    showToast('Ошибка копирования: ' + e.message, true)
  }
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
    _saveMode = data.save_mode || 'separate'
    _singleFile = data.single_file || ''
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

    // Restore single file input
    const singleInput = document.getElementById('single-file-path')
    if (singleInput && _singleFile) {
      singleInput.value = _singleFile
      setSingleFileStatus('ok', '✓ ' + _singleFile)
    }

    // Restore save mode radio + block visibility
    const radio = document.querySelector(`input[name="save-mode"][value="${_saveMode}"]`)
    if (radio) radio.checked = true
    _applySaveModeUI(_saveMode)

    renderModeCards(_currentMode)
    setModeStatus(_currentMode)

    // Restore timestamps toggle
    const tsToggle = document.getElementById('timestamps-toggle')
    if (tsToggle) tsToggle.checked = data.timestamps !== false
  } catch { /* non-critical */ }
}

async function setTimestamps(enabled) {
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timestamps: enabled }),
  }).catch(() => {})
  showToast(enabled ? 'Временные метки включены' : 'Временные метки отключены')
}

function _applySaveModeUI(mode) {
  document.getElementById('save-dir-block')?.classList.toggle('hidden', mode !== 'separate')
  document.getElementById('save-file-block')?.classList.toggle('hidden', mode !== 'single')
}

async function setSaveMode(mode) {
  _saveMode = mode
  _applySaveModeUI(mode)
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ save_mode: mode }),
  }).catch(() => {})
}

function setSingleFileStatus(type, msg) {
  const el = document.getElementById('single-file-status')
  if (!el) return
  el.textContent = msg
  el.className = 'path-status' + (type ? ' ' + type : '')
}

async function pickSingleFile() {
  const btn = event.currentTarget
  const orig = btn.textContent
  btn.textContent = '⏳ Открываю...'
  btn.disabled = true
  try {
    const r = await fetch('/api/pick-file', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Выберите или создайте файл для транскрипций' }),
    })
    if (r.status === 204 || r.status === 400) return
    if (!r.ok) { const e = await r.json(); showToast(fmtErr(e, r.status), true); return }
    const data = await r.json()
    document.getElementById('single-file-path').value = data.path
    _singleFile = data.path
    setSingleFileStatus('ok', '✓ ' + data.path)
    // Save to server
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ single_file: data.path }),
    })
    showToast('Файл сохранён')
  } catch (e) {
    showToast('Ошибка: ' + e.message, true)
  } finally {
    btn.textContent = orig
    btn.disabled = false
  }
}

async function resetSingleFile() {
  document.getElementById('single-file-path').value = ''
  _singleFile = ''
  setSingleFileStatus('', '')
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ single_file: '' }),
  }).catch(() => {})
  showToast('Сброшено')
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

// ─── YouTube API Key ──────────────────────────────────────────────

async function initApiKeyBanner() {
  try {
    const r = await fetch('/api/settings')
    const data = await r.json()
    const card = document.getElementById('api-key-card')
    const status = document.getElementById('api-key-status')
    const input = document.getElementById('api-key-input')
    card.classList.remove('hidden')

    if (data.youtube_key_set) {
      card.classList.add('has-key')
      status.textContent = '✓ Ключ подключён: ' + data.youtube_key_masked
      status.className = 'api-key-status ok'
      input.placeholder = 'Введи новый ключ чтобы заменить'
    } else {
      status.textContent = 'Ключ не задан — без него поиск и загрузка каналов не работают'
      status.className = 'api-key-status'
      // Auto-open instructions if no key
      const details = document.getElementById('instructions')
      if (details) details.open = true
    }
  } catch { /* non-critical */ }
}

function toggleKeyVisibility() {
  const inp = document.getElementById('api-key-input')
  inp.type = inp.type === 'password' ? 'text' : 'password'
}

async function saveApiKey() {
  const key = document.getElementById('api-key-input').value.trim()
  if (!key) { showToast('Введи API ключ', true); return }

  const btn = document.getElementById('api-key-save-btn')
  const status = document.getElementById('api-key-status')
  btn.textContent = 'Проверяю...'
  btn.disabled = true
  status.textContent = 'Проверяю ключ...'
  status.className = 'api-key-status'

  try {
    const r = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_api_key: key }),
    })
    const data = await r.json()
    if (!r.ok) {
      status.textContent = '✗ ' + fmtErr(data, r.status)
      status.className = 'api-key-status err'
      return
    }
    document.getElementById('api-key-card').classList.add('has-key')
    status.textContent = '✓ Ключ сохранён и проверен'
    status.className = 'api-key-status ok'
    document.getElementById('api-key-input').value = ''
    document.getElementById('api-key-input').placeholder = 'Введи новый ключ чтобы заменить'
    showToast('✓ YouTube API ключ сохранён')
  } catch (e) {
    status.textContent = '✗ ' + e.message
    status.className = 'api-key-status err'
  } finally {
    btn.textContent = 'Сохранить'
    btn.disabled = false
  }
}

// ─── Theme ───────────────────────────────────────────────────────

function initTheme() {
  const saved = localStorage.getItem('theme')
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true
  applyTheme(saved || (prefersDark ? 'dark' : 'light'))
}

function toggleTheme() {
  const current = document.body.dataset.theme || 'dark'
  applyTheme(current === 'dark' ? 'light' : 'dark')
}

function applyTheme(theme) {
  document.body.dataset.theme = theme
  localStorage.setItem('theme', theme)
  const btn = document.getElementById('theme-btn')
  if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙'
}

// Init settings on page load
window.addEventListener('load', () => {
  initTheme()
  loadSettings()
  initApiKeyBanner()
  refreshQuota()
})
