// Tiện ích dùng chung cho giao diện WCS B / WMS ảo
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

async function api(path, body) {
  const opt = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  return r.json();
}

const BADGE = {
  SUCCEEDED: 'ok', COMPLETED: 'ok', REPORTED: 'ok', DONE: 'ok', STORED: 'ok', EMPTY: 'ok', true: 'ok',
  FAILED: 'bad', DEAD: 'bad', REJECTED: 'bad', WMS_REJECTED: 'bad', ERROR: 'bad', LOCKED: 'bad', false: 'bad',
  CANCELLED: 'warn', SKIPPED: 'warn', CANCEL_REQUESTED: 'warn', WARN: 'warn', BUSY: 'warn', PENDING: 'warn',
  RUNNING: 'run', DISPATCHED: 'run', SENT: 'run', QUEUED: 'run', CREATED: 'run', IDENTIFIED: 'run', FULL: 'run',
};
const badge = v => `<span class="b ${BADGE[v] || ''}">${esc(v)}</span>`;

// cols: [[tiêu đề, key | (row)=>html]]
function table(rows, cols) {
  if (!rows || !rows.length) return '<div class="empty">Không có dữ liệu</div>';
  const head = cols.map(c => `<th>${esc(c[0])}</th>`).join('');
  const body = rows.map(r => '<tr>' + cols.map(c => {
    const v = typeof c[1] === 'function' ? c[1](r) : esc(typeof r[c[1]] === 'object' ? JSON.stringify(r[c[1]]) : r[c[1]]);
    return `<td>${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function tabs(names, render) {
  const nav = $('#tabs');
  let cur = localStorage.getItem(location.port + ':tab') || names[0][0];
  nav.innerHTML = names.map(n => `<button data-t="${n[0]}">${esc(n[1])}</button>`).join('');
  const show = async () => {
    nav.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.t === cur));
    try { $('#main').innerHTML = await render(cur); } catch (e) { $('#main').innerHTML = `<div class="err">${esc(e)}</div>`; }
  };
  nav.onclick = e => { if (e.target.dataset.t) { cur = e.target.dataset.t; try { localStorage.setItem(location.port + ':tab', cur); } catch (_) {} show(); } };
  show();
  // tab có form (vd. Tạo lệnh) không tự làm mới để không xoá chữ đang nhập
  setInterval(() => { if (!document.querySelector('#main form') && !document.querySelector('#main :focus')) show(); }, 3000);
  return show;
}

function toast(msg, bad) {
  const t = $('#toast'); t.textContent = typeof msg === 'string' ? msg : JSON.stringify(msg);
  t.className = bad ? 'show bad' : 'show'; setTimeout(() => t.className = '', 4000);
}

function msgTable(rows) {
  return table(rows, [['Giờ', 't'], ['Chiều', r => badge(r.dir)], ['Bên', 'peer'], ['Đường dẫn', 'path'],
    ['HTTP', 'status'], ['ms', 'ms'],
    ['Nội dung', r => `<details><summary>xem</summary><pre>${esc(JSON.stringify(r.body, null, 1))}</pre><pre>${esc(JSON.stringify(r.resp, null, 1))}</pre></details>`]]);
}
