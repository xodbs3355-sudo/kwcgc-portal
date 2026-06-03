// ── 검토 시작 시 오버레이 + 진행 막대 + 상태 텍스트 순환 ──────────
const reviewForm = document.getElementById('review-form');
const reviewOverlay = document.getElementById('review-overlay');
const reviewProgressBar = document.getElementById('review-progress-bar');
const reviewStatusText = document.getElementById('review-status-text');

// 검토 강제 종료 + 30초 경과 시 재시도 알림
const REVIEW_TIMEOUT_ALERT_MS = 30000;
let reviewTimeoutAlertTimer = null;

function showReviewTimeoutAlert() {
  const a = document.getElementById('review-timeout-alert');
  if (a) a.hidden = false;
}
function hideReviewTimeoutAlert() {
  const a = document.getElementById('review-timeout-alert');
  if (a) a.hidden = true;
}
function abortReview() {
  if (reviewTimeoutAlertTimer) clearTimeout(reviewTimeoutAlertTimer);
  window.location.reload();
}
(function bindReviewOverlayButtons() {
  const closeBtn = document.getElementById('review-overlay-close');
  if (closeBtn) closeBtn.addEventListener('click', abortReview);
  const retryBtn = document.getElementById('review-retry-btn');
  if (retryBtn) retryBtn.addEventListener('click', abortReview);
  const keepBtn = document.getElementById('review-keep-waiting-btn');
  if (keepBtn) keepBtn.addEventListener('click', hideReviewTimeoutAlert);
})();

// 서류별 검토 항목 — 회색 안내 텍스트 순환용 (시각 효과)
// 실제 검토는 백엔드에서 병렬 처리되며, 이 목록은 사용자 체감용 progress.
const REVIEW_FLOW = {
  doc01: { name: "준공계", items: ["서류 첨부 여부", "공사명 확인", "준공일자 확인", "준공금액 확인", "서명/날인 확인"] },
  doc02: { name: "하도급 공종별 작업내역서", items: ["서류 첨부 여부", "공공측량 용역 항목 존재", "타 테이블 빈 상태", "수량 1식 여부", "계약단가 25,000원 여부", "요율 10% 및 계 27,500원 여부"] },
  doc03: { name: "기밀시험 데이터", items: ["서류 첨부 여부", "압력 유지 여부"] },
  doc04: { name: "융착 데이터", items: ["서류 첨부 확인"] },
  doc10: { name: "자재성적서", items: ["서류 첨부 확인"] },
  doc05: { name: "교육일지/작업일보/스케치도면", items: ["작업일보", "안전보건환경 교육 및 TBM일지", "교육 및 TBM 사진", "스케치도면"] },
  doc06: { name: "산업안전보건관리비 내역서", items: ["산업안전보건관리비 사용내역서", "항목 별 사용내역", "사진대지", "세금계산서", "거래명세서", "지급대장"] },
  doc09: { name: "화재위험작업허가서", items: ["서류 첨부 여부", "공사명 확인", "준공일자 확인", "준공금액 확인", "서명/날인 확인"] },
  doc07: { name: "저심도배관현황", items: ["서류 첨부 여부", "깊이 기록", "위치 표기"] },
  doc08: { name: "기타서류", items: ["서류 첨부 여부"] }
};
const DOC_ORDER = ["doc01", "doc02", "doc03", "doc04", "doc10", "doc05", "doc06", "doc09", "doc07", "doc08"];

function buildReviewSteps() {
  // 해당없음 체크된 서류는 제외
  const skipped = new Set();
  document.querySelectorAll('.skip-check:checked').forEach(cb => {
    if (cb.dataset.docId) skipped.add(cb.dataset.docId);
  });
  const steps = [];
  for (const docId of DOC_ORDER) {
    if (skipped.has(docId)) continue;
    const doc = REVIEW_FLOW[docId];
    if (!doc) continue;
    for (const item of doc.items) {
      steps.push(`${doc.name} 검토 중 - ${item}`);
    }
  }
  return steps;
}

if (reviewForm && reviewOverlay) {
  reviewForm.addEventListener('submit', () => {
    // ── 입력값 동기 보장 (race 해결) ──
    // blur 시 fetch /project-info 가 페이지 unload 로 취소될 수 있어,
    // 검토 시작 시점에 모든 project_info 입력값을 hidden 으로 함께 전송.
    // 백엔드 review() 가 form 에서 받은 값으로 session 을 먼저 갱신 후 검토.
    document.querySelectorAll('.project-info-input').forEach(input => {
      const field = input.dataset.field;
      if (!field) return;
      // 금액 계열은 콤마 표기로 들어가 있어 그대로 전송 (백엔드가 콤마 제거 처리)
      let hidden = reviewForm.querySelector(`input[type="hidden"][name="${field}"]`);
      if (!hidden) {
        hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = field;
        reviewForm.appendChild(hidden);
      }
      hidden.value = input.value;
    });
    // PLP / 영구신청수수료 체크박스
    const checkboxFields = [
      { id: 'pi-plp', name: 'plp' },
      { id: 'pi-permit-fee', name: 'permit_fee' },
    ];
    checkboxFields.forEach(({ id, name }) => {
      const cb = document.getElementById(id);
      if (!cb) return;
      let hidden = reviewForm.querySelector(`input[type="hidden"][name="${name}"]`);
      if (!hidden) {
        hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = name;
        reviewForm.appendChild(hidden);
      }
      hidden.value = cb.checked ? 'true' : 'false';
    });

    reviewOverlay.hidden = false;
    // 30초 경과 후 재시도 알림 표시
    if (reviewTimeoutAlertTimer) clearTimeout(reviewTimeoutAlertTimer);
    hideReviewTimeoutAlert();
    reviewTimeoutAlertTimer = setTimeout(showReviewTimeoutAlert, REVIEW_TIMEOUT_ALERT_MS);
    // 진행 막대 — 12초에 걸쳐 95%까지 ease-out으로 채움
    if (reviewProgressBar) {
      reviewProgressBar.style.transition = 'none';
      reviewProgressBar.style.width = '0%';
      requestAnimationFrame(() => {
        reviewProgressBar.style.transition = 'width 12s cubic-bezier(0.1, 0.7, 0.1, 1)';
        reviewProgressBar.style.width = '95%';
      });
    }
    // 상태 텍스트 순환 — 500ms 간격, 끝까지 가면 다시 처음으로
    if (reviewStatusText) {
      const steps = buildReviewSteps();
      if (steps.length > 0) {
        let idx = 0;
        reviewStatusText.textContent = steps[0];
        setInterval(() => {
          idx = (idx + 1) % steps.length;
          reviewStatusText.textContent = steps[idx];
        }, 500);
      }
    }
    // 버튼 비활성
    const btn = reviewForm.querySelector('.btn-review');
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.7';
    }
  });
}

// ── Gemini 워밍업 — 사용자가 검토 시작 누르기 전 미리 호출 ──────────
// 검토 시작 버튼 hover / 첫 파일 업로드 등으로 트리거하여 cold start 줄임
let _warmupTriggered = false;
function triggerWarmup() {
  if (_warmupTriggered) return;
  _warmupTriggered = true;
  fetch('/warmup', { method: 'POST' }).catch(() => {});
}
document.querySelectorAll('.btn-review').forEach(btn => {
  btn.addEventListener('mouseenter', triggerWarmup, { once: true });
  btn.addEventListener('focus', triggerWarmup, { once: true });
});
// 파일 업로드 발생 시점에도 한 번 warmup (검토까지 일반적으로 5초 이상 소요됨)
document.querySelectorAll('.file-input').forEach(input => {
  input.addEventListener('change', triggerWarmup, { once: true });
});

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

const MONEY_FIELDS = new Set(['amount', 'land_fee', 'safety_fee']);

document.querySelectorAll('.project-info-input').forEach((input) => {
  // select는 change에서 즉시 저장 (blur 기다리지 않음)
  const isSelect = input.tagName === 'SELECT';
  const saveEvent = isSelect ? 'change' : 'blur';
  input.addEventListener(saveEvent, async () => {
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

// ── 영구신청수수료 체크박스 (해당 시 Check, 단순 ON/OFF 저장) ──────
const permitFeeCheck = document.getElementById('pi-permit-fee');
if (permitFeeCheck) {
  permitFeeCheck.addEventListener('change', async () => {
    const fd = new FormData();
    fd.append('field', 'permit_fee');
    fd.append('value', permitFeeCheck.checked ? 'true' : 'false');
    try { await fetch('/project-info', { method: 'POST', body: fd }); } catch (_) {}
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
