// ── 검토 시작 시 오버레이 표시 ────────────────────────────────────
const reviewForm = document.getElementById('review-form');
const reviewOverlay = document.getElementById('review-overlay');
if (reviewForm && reviewOverlay) {
  reviewForm.addEventListener('submit', () => {
    reviewOverlay.hidden = false;
    // 버튼도 비활성 + 텍스트 변경
    const btn = reviewForm.querySelector('.btn-review');
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.7';
    }
  });
}

// ── 파일 업로드 (AJAX) ───────────────────────────────────────────
document.querySelectorAll('.file-input').forEach((input) => {
  input.addEventListener('change', async (e) => {
    const docId = input.dataset.docId;
    const files = input.files;
    if (!files || files.length === 0) return;

    // 업로드 중 placeholder 칩 표시
    const pendingNames = Array.from(files).map(f => f.name);
    addPendingChips(docId, pendingNames);

    const fd = new FormData();
    for (const f of files) fd.append('files', f);

    try {
      const res = await fetch(`/upload/${docId}`, { method: 'POST', body: fd });
      const data = await res.json();
      if (data.ok) {
        renderChips(docId, data.files);
      } else {
        alert('업로드 실패: ' + (data.error || '알 수 없는 오류'));
        removePendingChips(docId);
      }
    } catch (err) {
      alert('네트워크 오류: ' + err.message);
      removePendingChips(docId);
    }
    input.value = '';
  });
});

// ── 파일 삭제 ─────────────────────────────────────────────────────
document.querySelector('.doc-table').addEventListener('click', async (e) => {
  const delBtn = e.target.closest('.file-chip-del');
  if (!delBtn) return;
  // 업로드 중 취소 버튼은 별도 핸들러
  if (delBtn.classList.contains('file-chip-cancel')) return;
  const docId = delBtn.dataset.docId;
  const idx = parseInt(delBtn.dataset.idx, 10);
  if (!docId || isNaN(idx)) return;

  try {
    const res = await fetch(`/remove/${docId}/${idx}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) renderChips(docId, data.files);
    else alert('삭제 실패: ' + (data.error || ''));
  } catch (err) {
    alert('삭제 실패: ' + err.message);
  }
});

// ── 준공 정보 입력 (blur 시 저장, 금액은 콤마 포맷) ─────────────
function formatAmount(input) {
  const raw = input.value.replace(/[^\d]/g, '');
  if (raw) {
    input.value = parseInt(raw, 10).toLocaleString('ko-KR');
  }
}

const MONEY_FIELDS = new Set(['amount', 'land_fee']);

document.querySelectorAll('.project-info-input').forEach((input) => {
  input.addEventListener('blur', async () => {
    // 금액 계열 필드면 콤마 포맷 후 저장
    if (MONEY_FIELDS.has(input.dataset.field)) {
      formatAmount(input);
    }
    const fd = new FormData();
    fd.append('field', input.dataset.field);
    fd.append('value', input.value);
    try {
      await fetch('/project-info', { method: 'POST', body: fd });
    } catch (_) {}
  });

  // 금액 계열: Enter 시 콤마 포맷 + 저장 (blur 트리거)
  if (MONEY_FIELDS.has(input.dataset.field)) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.blur();
      }
    });
  }
});

// ── PLP 체크박스 ↔ 화재위험작업허가서 해당없음 연동 ──────────────
const plpCheck = document.getElementById('pi-plp');
const fireSkip = document.querySelector('.skip-check[data-doc-id="doc09"]');
const fireRow  = fireSkip ? fireSkip.closest('tr') : null;

async function syncSkipServer(docId, checked) {
  const fd = new FormData();
  fd.append('skip', checked ? 'true' : 'false');
  try { await fetch(`/skip/${docId}`, { method: 'POST', body: fd }); } catch (_) {}
}

if (plpCheck) {
  plpCheck.addEventListener('change', async () => {
    // 서버에 PLP 상태 저장
    const fd = new FormData();
    fd.append('field', 'plp');
    fd.append('value', plpCheck.checked ? 'true' : 'false');
    try { await fetch('/project-info', { method: 'POST', body: fd }); } catch (_) {}

    // PLP 체크 시 → 화재위험 해당없음 해제 (검토 필요)
    // PLP 해제 시 → 화재위험 해당없음 다시 체크 (기본 상태 복원)
    if (fireSkip && fireRow) {
      fireSkip.checked = !plpCheck.checked;
      fireRow.classList.toggle('is-skipped', fireSkip.checked);
      syncSkipServer('doc09', fireSkip.checked);
    }
  });
}

// ── 해당없음 체크박스 ─────────────────────────────────────────────
document.querySelectorAll('.skip-check').forEach((cb) => {
  cb.addEventListener('change', async () => {
    const docId = cb.dataset.docId;
    const fd = new FormData();
    fd.append('skip', cb.checked ? 'true' : 'false');

    // 행 시각 표시
    const row = cb.closest('tr');
    if (row) row.classList.toggle('is-skipped', cb.checked);

    try {
      await fetch(`/skip/${docId}`, { method: 'POST', body: fd });
    } catch (_) {}
  });
});

// ── 행 클릭 → 활성화 (클립보드 paste 대상) ───────────────────────
let activeDocId = null;

document.querySelectorAll('.doc-table tbody tr').forEach((row) => {
  row.addEventListener('click', (e) => {
    // input / button / label / checkbox 등 클릭은 활성화에서 제외
    if (e.target.closest('input, textarea, button, label')) return;
    // 해당없음 체크된 행은 활성화 불가
    if (row.classList.contains('is-skipped')) return;
    setActiveRow(row);
  });
});

function setActiveRow(row) {
  document.querySelectorAll('.doc-table tbody tr.is-active').forEach((r) => {
    r.classList.remove('is-active');
  });
  if (row) {
    row.classList.add('is-active');
    activeDocId = row.dataset.docId;
  } else {
    activeDocId = null;
  }
}

// ── 클립보드 paste (Ctrl+V) → 활성 행에 파일 첨부 ────────────────
document.addEventListener('paste', async (e) => {
  // 텍스트 입력 중인 input/textarea 에서는 기본 paste 동작 유지
  if (e.target.matches('input, textarea')) return;

  const items = e.clipboardData ? e.clipboardData.items : null;
  if (!items) return;

  // 클립보드에서 파일만 추출
  const files = [];
  for (const item of items) {
    if (item.kind === 'file') {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (files.length === 0) return;

  e.preventDefault();

  if (!activeDocId) {
    alert('행을 먼저 클릭해서 선택한 다음 Ctrl+V 로 붙여넣으세요.');
    return;
  }

  const fd = new FormData();
  const pendingNames = [];
  for (const f of files) {
    // 이미지 캡처 등 기본 파일명(image.png) 은 타임스탬프 부여
    let name = f.name || '';
    if (!name || /^image\.(png|jpg|jpeg)$/i.test(name)) {
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const ext = f.type === 'image/png' ? 'png'
                : (f.type === 'image/jpeg' || f.type === 'image/jpg') ? 'jpg'
                : (f.type === 'application/pdf') ? 'pdf'
                : 'bin';
      name = `clipboard-${ts}.${ext}`;
    }
    fd.append('files', f, name);
    pendingNames.push(name);
  }

  // 업로드 중 placeholder 칩 표시
  addPendingChips(activeDocId, pendingNames);

  try {
    const res = await fetch(`/upload/${activeDocId}`, { method: 'POST', body: fd });
    const data = await res.json();
    if (data.ok) {
      renderChips(activeDocId, data.files);
    } else {
      alert('업로드 실패: ' + (data.error || '알 수 없는 오류'));
      removePendingChips(activeDocId);
    }
  } catch (err) {
    alert('업로드 오류: ' + err.message);
    removePendingChips(activeDocId);
  }
});

// ── pending 칩 헬퍼 ─────────────────────────────────────────────
function addPendingChips(docId, names) {
  const container = document.querySelector(`[data-chips-for="${docId}"]`);
  if (!container) return;
  for (const name of names) {
    const chip = document.createElement('span');
    chip.className = 'file-chip file-chip-pending';
    chip.innerHTML = `
      <span class="file-chip-spinner"></span>
      <span class="file-chip-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
      <button type="button" class="file-chip-del file-chip-cancel"
              title="취소">×</button>
    `;
    container.appendChild(chip);
  }
  updateFileCount(docId);
}
function removePendingChips(docId) {
  const container = document.querySelector(`[data-chips-for="${docId}"]`);
  if (!container) return;
  container.querySelectorAll('.file-chip-pending').forEach(c => c.remove());
  updateFileCount(docId);
}

// pending 칩 × 클릭 → 그 칩만 제거 (서버 요청 보내봤자 응답 와도 무시되니 표시만 정리)
document.addEventListener('click', (e) => {
  const cancelBtn = e.target.closest('.file-chip-cancel');
  if (!cancelBtn) return;
  const chip = cancelBtn.closest('.file-chip');
  const container = chip ? chip.closest('.file-chips') : null;
  if (chip) chip.remove();
  if (container) {
    const did = container.dataset.chipsFor;
    if (did) updateFileCount(did);
  }
});

// ── 파일 개수 배지 ────────────────────────────────────────────────
function updateFileCount(docId) {
  const container = document.querySelector(`[data-chips-for="${docId}"]`);
  if (!container) return;
  const hintBox = container.closest('.hint-box');
  if (!hintBox) return;
  // 기존 배지 제거
  hintBox.querySelectorAll('.file-count-badge').forEach(b => b.remove());
  const count = container.querySelectorAll('.file-chip').length;
  if (count > 0) {
    const badge = document.createElement('span');
    badge.className = 'file-count-badge';
    badge.textContent = `${count}건`;
    hintBox.appendChild(badge);
  }
}

// 페이지 로드 시 모든 행 카운트 초기화
document.querySelectorAll('.file-chips').forEach((c) => {
  const did = c.dataset.chipsFor;
  if (did) updateFileCount(did);
});

// ── 칩 렌더링 ─────────────────────────────────────────────────────
function renderChips(docId, fileNames) {
  const container = document.querySelector(`[data-chips-for="${docId}"]`);
  if (!container) return;
  container.innerHTML = '';
  fileNames.forEach((name, idx) => {
    const chip = document.createElement('span');
    chip.className = 'file-chip';
    chip.dataset.idx = String(idx);
    chip.innerHTML = `
      <span class="file-chip-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
      <button type="button" class="file-chip-del" data-doc-id="${docId}" data-idx="${idx}">×</button>
    `;
    container.appendChild(chip);
  });
  updateFileCount(docId);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
