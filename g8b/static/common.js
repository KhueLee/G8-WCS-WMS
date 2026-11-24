// Tiện ích dùng chung cho giao diện WCS B / WMS ảo
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = n => (n ?? 0).toLocaleString('vi-VN');

async function api(path, body, method) {
  const opt = body === undefined ? {} : {method: method || 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  return r.json();
}

const BADGE = {
  SUCCEEDED: 'ok', COMPLETED: 'ok', REPORTED: 'ok', DONE: 'ok', STORED: 'ok', EMPTY: 'ok', true: 'ok', MANUAL: 'run',
  FAILED: 'bad', DEAD: 'bad', REJECTED: 'bad', WMS_REJECTED: 'bad', ERROR: 'bad', LOCKED: 'bad', false: 'bad',
  CANCELLED: 'warn', SKIPPED: 'warn', CANCEL_REQUESTED: 'warn', WARN: 'warn', BUSY: 'warn', PENDING: 'warn', CLOSED: 'warn',
  RUNNING: 'run', DISPATCHED: 'run', SENT: 'run', QUEUED: 'run', CREATED: 'run', IDENTIFIED: 'run', FULL: 'run', SHIPPED: 'ok',
};
const badge = v => `<span class="b ${BADGE[v] || ''}">${esc(v)}</span>`;
// trạng thái: luôn icon + chữ, không dùng màu một mình
const status = (ok, good = 'Kết nối', bad = 'Lỗi') => ok === true ? `<span class="st good"><i>●</i>${good}</span>`
  : ok === false ? `<span class="st crit"><i>▲</i>${bad}</span>` : `<span class="st unk"><i>○</i>Chưa rõ</span>`;

// cols: [[tiêu đề, key | (row)=>html, 'num'?]]
function table(rows, cols) {
  if (!rows || !rows.length) return '<div class="empty">Không có dữ liệu</div>';
  const head = cols.map(c => `<th class="${c[2] || ''}">${esc(c[0])}</th>`).join('');
  const body = rows.map(r => '<tr>' + cols.map(c => {
    const v = typeof c[1] === 'function' ? c[1](r) : esc(typeof r[c[1]] === 'object' && r[c[1]] !== null ? JSON.stringify(r[c[1]]) : r[c[1]]);
    return `<td class="${c[2] || ''}">${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<div class="tw"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// tabs: [[id, nhãn] | ['-', nhóm]]; render(id) -> {title, html} | html
function tabs(list, render, opts = {}) {
  const nav = $('#tabs');
  const ids = list.filter(t => t[0] !== '-').map(t => t[0]);
  let cur = ids[0];
  try { const s = localStorage.getItem(location.port + ':tab'); if (ids.includes(s)) cur = s; } catch (_) {}
  const h = location.hash.slice(1); if (ids.includes(h)) cur = h;  // mở thẳng 1 tab: /#cfg
  nav.innerHTML = list.map(t => t[0] === '-' ? `<div class="grp">${esc(t[1])}</div>` : `<button data-t="${t[0]}">${esc(t[1])}</button>`).join('');
  const show = async () => {
    nav.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.t === cur));
    try {
      const out = await render(cur);
      const label = list.find(t => t[0] === cur)[1];
      $('#main').innerHTML = `<h2>${esc(label)}</h2>` + out;
      if (opts.after) opts.after(cur);
    } catch (e) { $('#main').innerHTML = `<div class="err">${esc(e)}</div>`; }
  };
  nav.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    cur = b.dataset.t; try { localStorage.setItem(location.port + ':tab', cur); } catch (_) {} show();
  };
  show();
  // tab có form (đặt lệnh, cấu hình) không tự làm mới để không mất chữ đang nhập
  setInterval(() => { if (!document.querySelector('#main form, #main .noauto') && !document.querySelector('#main :focus')) show(); }, 3000);
  return show;
}

function toast(msg, bad) {
  const t = $('#toast'); t.textContent = typeof msg === 'string' ? msg : (msg.msg || JSON.stringify(msg));
  t.className = bad ? 'show bad' : 'show'; clearTimeout(t._h); t._h = setTimeout(() => t.className = '', 5000);
}
const done = r => { toast(r, r && r.ok === false); return r; };

function msgTable(rows) {
  return table(rows, [['Giờ', 't'], ['Chiều', r => badge(r.dir)], ['Bên', 'peer'], ['Đường dẫn', 'path'],
    ['HTTP', 'status'], ['ms', 'ms', 'num'],
    ['Nội dung', r => `<details><summary>xem</summary><pre>${esc(JSON.stringify(r.body, null, 1))}</pre><pre>${esc(JSON.stringify(r.resp, null, 1))}</pre></details>`]]);
}

// ---------------------------------------------------------------- dashboard
const tile = (k, v, s = '') => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;

// tooltip dùng chung cho các phần tử có data-tip
function initTip() {
  if ($('#tip')) return;
  const tip = document.createElement('div'); tip.id = 'tip'; document.body.appendChild(tip);
  document.addEventListener('mousemove', e => {
    const el = e.target.closest('[data-tip]');
    if (!el) { tip.className = ''; return; }
    tip.innerHTML = el.dataset.tip; tip.className = 'show';
    const w = tip.offsetWidth, x = Math.min(e.clientX + 12, innerWidth - w - 8);
    tip.style.left = x + 'px'; tip.style.top = (e.clientY - tip.offsetHeight - 10) + 'px';
  });
}

// cột theo giờ (1 chuỗi số liệu, không cần chú giải: tiêu đề card gọi tên)
function hourBars(vals, unit) {
  const W = 720, H = 190, L = 34, B = 22, T = 8, n = vals.length;
  const max = Math.max(1, ...vals), step = niceStep(max), top = Math.ceil(max / step) * step;
  const cw = (W - L) / n, bw = Math.max(4, cw - 4);
  const y = v => T + (H - T - B) * (1 - v / top);
  let g = '';
  for (let v = 0; v <= top; v += step) g += `<line class="gl" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  const nowH = new Date().getHours();
  vals.forEach((v, i) => {
    const x = L + i * cw + (cw - bw) / 2, h = (H - T - B) - (y(v) - T), y0 = H - B;
    const r = Math.min(4, h, bw / 2);
    const path = v > 0 ? `<path class="bar" d="M${x},${y0} V${y0 - h + r} Q${x},${y0 - h} ${x + r},${y0 - h} H${x + bw - r} Q${x + bw},${y0 - h} ${x + bw},${y0 - h + r} V${y0} Z"/>` : '';
    g += `<g data-tip="<b>${String(i).padStart(2, '0')}:00–${String(i).padStart(2, '0')}:59</b><br>${fmt(v)} ${esc(unit)}"><rect class="hit" x="${L + i * cw}" y="${T}" width="${cw}" height="${H - T}"/>${path}</g>`;
    if (i % 3 === 0 || i === nowH) g += `<text class="ax" x="${L + i * cw + cw / 2}" y="${H - 6}" text-anchor="middle" ${i === nowH ? 'style="font-weight:700"' : ''}>${i}h</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(unit)} theo giờ hôm nay">${g}</svg>`;
}
function niceStep(max) { const raw = max / 4, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p; return Math.max(1, (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p); }

// độ lấp đầy ô theo zone: Đầy / Robot đang tới / Khoá, phần còn lại là trống (nền)
const OCC = [['FULL', 'Đầy', 'var(--series-1)'], ['BUSY', 'Robot đang tới', 'var(--series-2)'], ['LOCKED', 'Khoá', 'var(--series-3)']];
function occupancy(occ) {
  const zones = Object.keys(occ || {}).sort();
  if (!zones.length) return '<div class="empty">Chưa nạp vị trí</div>';
  const legend = '<div class="legend">' + OCC.map(o => `<span><b style="background:${o[2]}"></b>${o[1]}</span>`).join('') +
    '<span><b style="background:var(--track)"></b>Trống</span></div>';
  return legend + zones.map(z => {
    const c = occ[z], total = Object.values(c).reduce((a, b) => a + b, 0) || 1;
    const segs = OCC.filter(o => c[o[0]]).map(o => `<div style="width:${100 * c[o[0]] / total}%;background:${o[2]}" data-tip="<b>Zone ${esc(z)}</b><br>${o[1]}: ${c[o[0]]}/${total}"></div>`).join('');
    const empty = c.EMPTY || 0;
    const rest = empty ? `<div style="width:${100 * empty / total}%;background:var(--track)" data-tip="<b>Zone ${esc(z)}</b><br>Trống: ${empty}/${total}"></div>` : '';
    const used = total - empty;
    return `<div class="occ"><div class="zn">Zone ${esc(z)}</div><div class="tr">${segs}${rest}</div><div class="nb">${used}/${total} ô có hàng</div></div>`;
  }).join('');
}

// ---------------------------------------------------------------- form cấu hình
// input gắn với đường dẫn trong object cấu hình: data-k="rcs.url", data-t="num|bool|list|str"
function fld(label, k, v, t = 'str', extra = '') {
  if (t === 'bool') return `<label class="ck"><input type="checkbox" data-k="${k}" data-t="bool" ${v ? 'checked' : ''}>${esc(label)}</label>`;
  const val = t === 'list' ? (v || []).join(', ') : (v ?? '');
  return `<label class="fl">${esc(label)}<input data-k="${k}" data-t="${t}" value="${esc(val)}" ${t === 'num' ? 'type="number" step="any"' : ''} ${extra}></label>`;
}
function readFields(root, obj) {
  $$('[data-k]', root).forEach(el => {
    const path = el.dataset.k.split('.'); let o = obj;
    path.slice(0, -1).forEach(p => o = o[p] = o[p] || {});
    const t = el.dataset.t, v = el.value;
    o[path.at(-1)] = t === 'bool' ? el.checked : t === 'num' ? Number(v) : t === 'list' ? v.split(',').map(s => s.trim()).filter(Boolean).map(s => isNaN(s) ? s : Number(s)) : v.trim();
  });
  return obj;
}
// bảng sửa được: cols [[key, nhãn, kiểu]]
function editTable(id, rows, cols) {
  const tr = r => '<tr>' + cols.map(c => c[2] === 'bool' ? `<td><input type="checkbox" data-c="${c[0]}" ${r[c[0]] !== false ? 'checked' : ''}></td>`
    : c[2] === 'sel' ? `<td><select data-c="${c[0]}">${c[3].map(o => `<option ${o === r[c[0]] ? 'selected' : ''}>${o}</option>`).join('')}</select></td>`
    : `<td><input data-c="${c[0]}" value="${esc(r[c[0]] ?? '')}" ${c[2] === 'wide' ? 'style="min-width:260px"' : ''}></td>`).join('') +
    `<td><button type="button" class="a d" onclick="this.closest('tr').remove()">Xoá</button></td></tr>`;
  return `<div class="tw"><table id="${id}" data-cols='${JSON.stringify(cols.map(c => [c[0], c[2]]))}'><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join('')}<th></th></tr></thead>
    <tbody>${rows.map(tr).join('')}</tbody></table></div>
    <button type="button" class="a" style="margin-top:6px" onclick="addRow('${id}')">+ Thêm dòng</button>`;
}
function addRow(id) {
  const t = $('#' + id), cols = JSON.parse(t.dataset.cols);
  const row = document.createElement('tr');
  row.innerHTML = cols.map(c => c[1] === 'bool' ? `<td><input type="checkbox" data-c="${c[0]}" checked></td>`
    : c[1] === 'sel' ? `<td>${t.querySelector(`select[data-c="${c[0]}"]`)?.outerHTML.replace(/ selected/g, '') || `<select data-c="${c[0]}"><option>RACK</option><option>STATION</option></select>`}</td>`
    : `<td><input data-c="${c[0]}" ${c[1] === 'wide' ? 'style="min-width:260px"' : ''}></td>`).join('') +
    `<td><button type="button" class="a d" onclick="this.closest('tr').remove()">Xoá</button></td>`;
  t.querySelector('tbody').appendChild(row);
}
function readTable(id) {
  return $$('#' + id + ' tbody tr').map(tr => Object.fromEntries($$('[data-c]', tr).map(el => [el.dataset.c, el.type === 'checkbox' ? el.checked : el.value.trim()])));
}

// file vị trí: zones <-> bảng (ranges dạng "S02:250-259:1-2; S03:1-5:1-4")
function zonesToRows(def) {
  return (def?.zones || []).map(z => ({zone: z.zone, kind: z.kind || 'RACK',
    ranges: (z.ranges || []).map(r => `${r.rack}:${r.cols[0]}-${r.cols[1]}:${r.levels[0]}-${r.levels[1]}`).join('; '),
    codes: (z.codes || []).join(', ')}));
}
function rowsToZones(rows) {
  const zones = rows.filter(r => r.zone).map(r => ({zone: r.zone, kind: r.kind || 'RACK',
    ranges: r.ranges.split(';').map(s => s.trim()).filter(Boolean).map(s => {
      const [rack, c, l] = s.split(':'); const [c1, c2] = c.split('-').map(Number); const [l1, l2] = (l || '1-1').split('-').map(Number);
      return {rack: rack.trim(), cols: [c1, c2 ?? c1], levels: [l1, l2 ?? l1]};
    }),
    codes: r.codes.split(',').map(s => s.trim()).filter(Boolean)}));
  return {zones};
}
function locationEditor(d) {
  return `<div class="card"><h3>Danh sách vị trí</h3>
  <div class="hint">File đang dùng: <b>${esc(d.file)}</b> · ${fmt(d.count)} vị trí trong DB. Mã dạng Rack-Cột-Tầng (vd. S02-257-1), trùng tên điểm trên RCS.
  Dải: <code>S02:250-259:1-2</code> = S02-250-1 … S02-259-2, nhiều dải cách nhau dấu ;</div>
  ${d.editable ? editTable('zt', zonesToRows(d.def), [['zone', 'Zone', 'str'], ['kind', 'Loại', 'sel', ['RACK', 'STATION']], ['ranges', 'Dải ô (Rack:Cột:Tầng)', 'wide'], ['codes', 'Mã lẻ (cách nhau dấu phẩy)', 'wide']])
    + `<div class="row" style="margin-top:10px"><button type="button" class="p" onclick="saveZones()">Lưu danh sách vị trí</button></div>`
    : '<div class="hint">File Excel / CSV: sửa trong file rồi tải lên lại.</div>'}
  <div class="row" style="margin-top:14px"><label class="fl">Tải file Excel (cột "Location Code", tuỳ chọn cột "Zone") / CSV / JSON<input type="file" id="locfile" accept=".xlsx,.xlsm,.csv,.json"></label>
  <button type="button" class="a" onclick="uploadLoc()">Tải lên và nạp</button></div></div>`;
}
async function saveZones() { done(await api('/api/locations/def', rowsToZones(readTable('zt')), 'PUT')); }
async function uploadLoc() {
  const f = $('#locfile').files[0]; if (!f) return toast('Chọn file trước', true);
  const b64 = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
  done(await api('/api/locations/upload', {name: f.name, b64}));
}
