// ── 파일 업로드 (AJAX) ───────────────────────────────────────────
document.querySelectorAll('.file-input').forEach((input) => {
  input.addEventListener('change', async (e) => {
    const docId = input.dataset.docId;
    const files = input.files;
    if (!files || files.length === 0) return;

    const fd = new FormData();
    for (const f of files) fd.append('files', f);

    try {
      const res = await fetch(`/upload/${docId}`, { method: 'POST', body: fd });
      const data = await res.json();
      if (data.ok) {
        renderChips(docId, data.files);
      } else {
        alert('업로드 실패: ' + (data.error || '알 수 없는 오류'));
      }
    } catch (err) {
      alert('네트워크 오류: ' + err.message);
    }
    input.value = '';
  });
});

// ── 파일 삭제 ─────────────────────────────────────────────────────
document.querySelector('.doc-table').addEventListener('click', async (e) => {
  if (!e.target.classList.contains('file-chip-del')) return;
  const docId = e.target.dataset.docId;
  const idx = parseInt(e.target.dataset.idx, 10);

  try {
    const res = await fetch(`/remove/${docId}/${idx}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) renderChips(docId, data.files);
  } catch (err) {
    alert('삭제 실패: ' + err.message);
  }
});

// ── 특기사항 저장 (blur 시) ───────────────────────────────────────
document.querySelectorAll('.note-input').forEach((input) => {
  input.addEventListener('blur', async () => {
    const docId = input.dataset.docId;
    const fd = new FormData();
    fd.append('note', input.value);
    try {
      await fetch(`/note/${docId}`, { method: 'POST', body: fd });
    } catch (_) {}
  });
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
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
