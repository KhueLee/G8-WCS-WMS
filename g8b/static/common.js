// Khung giao diện chung của site G8 luồng B (WMS ảo + WCS B):
// menu trái theo nhóm (bấm nhóm để sổ chức năng con), hộp thoại lựa chọn, bảng, biểu đồ, form cấu hình,
// trang vị trí / kho. API gọi qua tiền tố app: /wms/... (WMS ảo), /wcs/... (WCS B) - server giao diện chuyển tiếp.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = n => (n ?? 0).toLocaleString('vi-VN');
const W = p => '/wms' + p;   // API WMS ảo
const C = p => '/wcs' + p;   // API WCS B

async function api(path, body, method) {
  const opt = body === undefined ? {} : {method: method || 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (r.status === 502) return {ok: false, msg: (await r.json()).msg || 'Không kết nối được app'};
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

// ---------------------------------------------------------------- site: menu nhóm + trang
const PAGES = {};      // id -> {group, label, app, render, after}
const GROUPS = [];     // [{key, label}]
let CUR = null;
function group(key, label) { GROUPS.push({key, label}); }
function page(id, grp, label, app, render, after) { PAGES[id] = {id, group: grp, label, app, render, after}; }

function navHtml() {
  let open = {};
  try { open = JSON.parse(localStorage.getItem('g8b:open') || '{}'); } catch (_) {}
  const cg = PAGES[CUR]?.group;
  return GROUPS.map(g => {
    const items = Object.values(PAGES).filter(p => p.group === g.key);
    if (!items.length) return '';
    const isOpen = open[g.key] ?? (g.key === cg);
    return `<div class="ng ${isOpen ? 'open' : ''}" data-g="${g.key}"><button class="gh" data-g="${g.key}"><span>${esc(g.label)}</span><i>${isOpen ? '▾' : '▸'}</i></button>
      <div class="gi">${items.map(p => `<button class="it ${p.id === CUR ? 'on' : ''}" data-p="${p.id}">${esc(p.label)}<small>${esc(p.app)}</small></button>`).join('')}</div></div>`;
  }).join('');
}

async function show() {
  const p = PAGES[CUR];
  $('#tabs').innerHTML = navHtml();
  if (!p) return;
  try {
    const out = await p.render();
    $('#main').innerHTML = `<h2>${esc(p.label)} <span class="b">${esc(p.app)}</span></h2>` + out;
    if (p.after) p.after();
  } catch (e) { $('#main').innerHTML = `<h2>${esc(p.label)}</h2><div class="err">${esc(e)}</div>`; }
}
const render = show;

function go(id) {
  if (!PAGES[id]) return;
  CUR = id;
  try { localStorage.setItem('g8b:page', id); } catch (_) {}
  if (location.hash.slice(1) !== id) history.replaceState(null, '', '#' + id);
  show();
}

function startSite(defaultPage) {
  initTip();
  const h = location.hash.slice(1);
  let saved = null;
  try { saved = localStorage.getItem('g8b:page'); } catch (_) {}
  CUR = PAGES[h] ? h : PAGES[saved] ? saved : defaultPage;
  $('#tabs').onclick = e => {
    const gh = e.target.closest('.gh'), it = e.target.closest('.it');
    if (gh) {   // bấm nhóm: sổ / thu chức năng con
      let open = {};
      try { open = JSON.parse(localStorage.getItem('g8b:open') || '{}'); } catch (_) {}
      const el = gh.parentElement, now = !el.classList.contains('open');
      el.classList.toggle('open', now); gh.querySelector('i').textContent = now ? '▾' : '▸';
      open[gh.dataset.g] = now;
      try { localStorage.setItem('g8b:open', JSON.stringify(open)); } catch (_) {}
    } else if (it) go(it.dataset.p);
  };
  window.addEventListener('hashchange', () => { const x = location.hash.slice(1); if (PAGES[x] && x !== CUR) go(x); });
  show();
  // trang có form (đặt lệnh, cấu hình) không tự làm mới để không mất chữ đang nhập
  setInterval(() => { if (!$('#dlg.show') && !document.querySelector('#main form, #main .noauto') && !document.querySelector('#main :focus')) show(); }, 3000);
}

// ---------------------------------------------------------------- thông báo / hộp thoại lựa chọn
function toast(msg, bad) {
  const t = $('#toast'); t.textContent = typeof msg === 'string' ? msg : (msg.msg || JSON.stringify(msg));
  t.className = bad ? 'show bad' : 'show'; clearTimeout(t._h); t._h = setTimeout(() => t.className = '', 5000);
}
const done = r => { toast(r, r && r.ok === false); return r; };

// dialog({title, body, buttons:[{label, value, kind:'p'|'d'|''}]}) -> Promise<{value, data}>; data = các input [name] trong body
function dialog({title, body = '', buttons = [{label: 'Đóng', value: null}]}) {
  let d = $('#dlg');
  if (!d) { d = document.createElement('div'); d.id = 'dlg'; document.body.appendChild(d); }
  d.innerHTML = `<div class="dbox" role="dialog" aria-modal="true"><h3>${esc(title)}</h3><div class="dbody">${body}</div>
    <div class="dbtn">${buttons.map((b, i) => `<button type="button" class="${b.kind === 'p' ? 'p' : 'a'} ${b.kind === 'd' ? 'd' : ''}" data-i="${i}">${esc(b.label)}</button>`).join('')}</div></div>`;
  d.className = 'show';
  return new Promise(res => {
    const close = v => {
      const data = Object.fromEntries($$('[name]', d).map(el => [el.name, el.type === 'checkbox' ? el.checked : el.value]));
      d.className = ''; d.innerHTML = ''; document.removeEventListener('keydown', key); res({value: v, data});
    };
    const key = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', key);
    d.onclick = e => {
      if (e.target === d) return close(null);
      const b = e.target.closest('[data-i]'); if (b) close(buttons[+b.dataset.i].value);
    };
  });
}
async function ask(title, msg, ok = 'Đồng ý', kind = 'p') {
  const r = await dialog({title, body: `<p>${msg}</p>`, buttons: [{label: 'Thôi', value: false}, {label: ok, value: true, kind}]});
  return r.value === true;
}

function msgTable(rows) {
  return table(rows, [['Giờ', 't'], ['Chiều', r => badge(r.dir)], ['Bên', 'peer'], ['Đường dẫn', 'path'],
    ['HTTP', 'status'], ['ms', 'ms', 'num'],
    ['Nội dung', r => `<details><summary>xem</summary><pre>${esc(JSON.stringify(r.body, null, 1))}</pre><pre>${esc(JSON.stringify(r.resp, null, 1))}</pre></details>`]]);
}

// ---------------------------------------------------------------- dashboard
const tile = (k, v, s = '') => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;

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
  const Wd = 720, H = 190, L = 34, B = 22, T = 8, n = vals.length;
  const max = Math.max(1, ...vals), step = niceStep(max), top = Math.ceil(max / step) * step;
  const cw = (Wd - L) / n, bw = Math.max(4, cw - 4);
  const y = v => T + (H - T - B) * (1 - v / top);
  let g = '';
  for (let v = 0; v <= top; v += step) g += `<line class="gl" x1="${L}" x2="${Wd}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  const nowH = new Date().getHours();
  vals.forEach((v, i) => {
    const x = L + i * cw + (cw - bw) / 2, h = (H - T - B) - (y(v) - T), y0 = H - B;
    const r = Math.min(4, h, bw / 2);
    const path = v > 0 ? `<path class="bar" d="M${x},${y0} V${y0 - h + r} Q${x},${y0 - h} ${x + r},${y0 - h} H${x + bw - r} Q${x + bw},${y0 - h} ${x + bw},${y0 - h + r} V${y0} Z"/>` : '';
    g += `<g data-tip="<b>${String(i).padStart(2, '0')}:00–${String(i).padStart(2, '0')}:59</b><br>${fmt(v)} ${esc(unit)}"><rect class="hit" x="${L + i * cw}" y="${T}" width="${cw}" height="${H - T}"/>${path}</g>`;
    if (i % 3 === 0 || i === nowH) g += `<text class="ax" x="${L + i * cw + cw / 2}" y="${H - 6}" text-anchor="middle" ${i === nowH ? 'style="font-weight:700"' : ''}>${i}h</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${Wd} ${H}" role="img" aria-label="${esc(unit)} theo giờ hôm nay">${g}</svg>`;
}
function niceStep(max) { const raw = max / 4, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p; return Math.max(1, (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p); }

// độ lấp đầy ô theo kho: Đầy / Robot đang tới / Khoá, phần còn lại là trống (nền)
const OCC = [['FULL', 'Đầy', 'var(--series-1)'], ['BUSY', 'Robot đang tới', 'var(--series-2)'], ['LOCKED', 'Khoá', 'var(--series-3)']];
function occupancy(occ) {
  const zones = Object.keys(occ || {}).sort();
  if (!zones.length) return '<div class="empty">Chưa nạp vị trí</div>';
  const legend = '<div class="legend">' + OCC.map(o => `<span><b style="background:${o[2]}"></b>${o[1]}</span>`).join('') +
    '<span><b style="background:var(--track)"></b>Trống</span></div>';
  return legend + zones.map(z => {
    const c = occ[z], total = Object.values(c).reduce((a, b) => a + b, 0) || 1;
    const segs = OCC.filter(o => c[o[0]]).map(o => `<div style="width:${100 * c[o[0]] / total}%;background:${o[2]}" data-tip="<b>Kho ${esc(z)}</b><br>${o[1]}: ${c[o[0]]}/${total}"></div>`).join('');
    const empty = c.EMPTY || 0;
    const rest = empty ? `<div style="width:${100 * empty / total}%;background:var(--track)" data-tip="<b>Kho ${esc(z)}</b><br>Trống: ${empty}/${total}"></div>` : '';
    return `<div class="occ"><div class="zn">Kho ${esc(z)}</div><div class="tr">${segs}${rest}</div><div class="nb">${total - empty}/${total} ô có hàng</div></div>`;
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
    : c[2] === 'sel' ? `<td><select data-c="${c[0]}">${c[3].map(o => `<option ${String(o) === String(r[c[0]]) ? 'selected' : ''}>${o}</option>`).join('')}</select></td>`
    : `<td><input data-c="${c[0]}" value="${esc(r[c[0]] ?? '')}" ${c[2] === 'wide' ? 'style="min-width:260px"' : ''}></td>`).join('') +
    `<td><button type="button" class="a d" onclick="this.closest('tr').remove()">Xoá</button></td></tr>`;
  return `<div class="tw"><table id="${id}" data-cols='${JSON.stringify(cols.map(c => [c[0], c[2], c[3] || null]))}'><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join('')}<th></th></tr></thead>
    <tbody>${rows.map(tr).join('')}</tbody></table></div>
    <button type="button" class="a" style="margin-top:6px" onclick="addRow('${id}')">+ Thêm dòng</button>`;
}
function addRow(id) {
  const t = $('#' + id), cols = JSON.parse(t.dataset.cols);
  const row = document.createElement('tr');
  row.innerHTML = cols.map(c => c[1] === 'bool' ? `<td><input type="checkbox" data-c="${c[0]}" checked></td>`
    : c[1] === 'sel' ? `<td><select data-c="${c[0]}">${(c[2] || []).map(o => `<option>${o}</option>`).join('')}</select></td>`
    : `<td><input data-c="${c[0]}" ${c[1] === 'wide' ? 'style="min-width:260px"' : ''}></td>`).join('') +
    `<td><button type="button" class="a d" onclick="this.closest('tr').remove()">Xoá</button></td>`;
  t.querySelector('tbody').appendChild(row);
}
function readTable(id) {
  return $$('#' + id + ' tbody tr').map(tr => Object.fromEntries($$('[data-c]', tr).map(el => [el.dataset.c, el.type === 'checkbox' ? el.checked : el.value.trim()])));
}

// ---------------------------------------------------------------- danh sách vị trí (form highbay RCS) + kho
// Danh sách vị trí và kho dùng chung file cho 2 app; site sửa qua WMS ảo (WCS B tự nạp lại khi file đổi).
const LOC = {q: '', wh: '', page: 1, size: 100, edit: null, imp: null};
const HB_FORM = [['code', 'Location Code'], ['node', 'Node'], ['area', 'Area Name'], ['level_height', 'Level Height'],
  ['coord', 'Location Coordinates'], ['layer', 'Location layer'], ['length', 'Location length (mm)'], ['width', 'Location width (mm)'],
  ['orientation', 'Location orientation(°)'], ['fork_height', 'Fork lifting height'], ['business_type', 'Business type'],
  ['carrier_model', 'Storage Carrier Model'], ['hl_model', 'High level storage model'], ['self_reg', 'Self regulating fork teeth']];
// mặc định khi nạp file: ~100 điểm thuộc S02 (kho lắp ráp LED trụ) và C (kho thành phẩm)
const IMPORT_DEFAULT = {S02: 50, C: 50};

async function locationPage() {
  const info = await api(W('/api/locations/info'));
  LOC.info = info;
  const whOpts = '<option value="">Tất cả kho</option>' + info.warehouses.map(w => `<option value="${esc(w.code)}" ${LOC.wh === w.code ? 'selected' : ''}>${esc(w.code)} – ${esc(w.name)} (${w.count})</option>`).join('');
  return `<div class="noauto">
  <div class="card"><h3>Đang quản lý ${fmt(info.count)} điểm · file ${esc(info.file)}</h3>
   <div class="hint">Form highbay của RCS (14 cột). WMS ảo và WCS B dùng chung file và danh mục kho, tự nạp lại khi file đổi.
   Điểm sàn (từ cấu hình line): ${info.stations.map(esc).join(', ') || '–'}.${info.unassigned ? ` <span class="st warn"><i>▲</i>${info.unassigned} điểm chưa thuộc kho nào</span>` : ''}</div>
   ${info.error ? `<div class="err">Lỗi đọc file: ${esc(info.error)}</div>` : ''}
   <div class="row"><a class="p" href="${W('/api/locations/export?fmt=raw')}" style="text-decoration:none;display:inline-block">Xuất Excel (dạng RAW)</a>
    <a class="a" href="${W('/api/locations/export?fmt=14')}" style="text-decoration:none;display:inline-block;padding:6px 10px">Xuất form 14 cột RCS</a>
    <button type="button" class="a" onclick="locReload()">Nạp lại từ file</button></div></div>
  <div class="card"><h3>Nạp file highbay mới</h3>
   <div class="hint">Nhận form 14 cột (Area_..._High Location.xlsx) hoặc bố cục RAW (như "2500 RAW.xlsx"). Chọn kho và số điểm cần quản lý,
   xem khác biệt với bộ hiện tại rồi áp dụng: điểm mới được thêm, điểm không còn bị xoá <b>kèm pallet gán ở điểm đó</b>.
   Kho được tạo đủ cho mọi tiền tố có trong file.</div>
   <div class="row"><label class="fl">File<input type="file" id="locfile" accept=".xlsx,.xlsm,.csv"></label>
    <button type="button" class="a" onclick="impRead()">1. Đọc file</button></div>
   <div id="impbox"></div></div>
  <div class="grid2">
   <div class="card" id="locform"></div>
   <div class="card"><h3>Thêm cả dải ô</h3><div class="hint">Mã sinh ra: Rack-Cột-Tầng; điểm tự vào kho theo tiền tố. Node: số node của cột đầu, mỗi cột sau cộng "bước" (để trống nếu chưa có).</div>
    <div class="row" id="bulk">
     <label class="fl">Rack<input data-b="rack" placeholder="S02" style="min-width:70px;width:80px"></label>
     <label class="fl">Cột từ<input data-b="col_from" type="number" style="width:80px"></label><label class="fl">đến<input data-b="col_to" type="number" style="width:80px"></label>
     <label class="fl">Tầng từ<input data-b="lv_from" type="number" value="1" style="width:70px;min-width:60px"></label><label class="fl">đến<input data-b="lv_to" type="number" value="1" style="width:70px;min-width:60px"></label>
     <label class="fl">Node cột đầu<input data-b="node_from" style="width:110px"></label><label class="fl">Bước node<input data-b="node_step" type="number" value="1" style="width:70px;min-width:60px"></label>
     <button type="button" class="p" onclick="locBulk()">Thêm dải</button></div></div>
  </div>
  <div class="card"><div class="row" style="margin-bottom:10px">
   <label class="fl">Kho<select id="locwh" onchange="LOC.wh=this.value;LOC.page=1;locList()">${whOpts}</select></label>
   <label class="fl">Tìm theo mã (đầu mã)<input id="locq" value="${esc(LOC.q)}" placeholder="S02-25" oninput="LOC.q=this.value;LOC.page=1;locList()"></label>
   <span class="hint" id="loccount"></span></div><div id="loclist"></div></div></div>`;
}
function locFormHtml() {
  const r = LOC.edit || {};
  const dv = k => k === 'area' ? 'G8_NVL_SLAM' : k === 'business_type' ? 'Forklift' : k === 'length' || k === 'width' ? '1440.0' : k === 'layer' ? 1 : k === 'self_reg' ? 'false' : k === 'orientation' || k === 'fork_height' ? 0 : '';
  return `<h3>${LOC.edit ? 'Sửa vị trí ' + esc(LOC.edit.code) : 'Thêm vị trí'}</h3><div class="row" id="hbf">` +
    HB_FORM.map(([k, t]) => `<label class="fl">${esc(t)}<input data-h="${k}" value="${esc(r[k] ?? dv(k))}" style="width:150px"></label>`).join('') +
    `</div><div class="row" style="margin-top:10px"><button type="button" class="p" onclick="locSave()">Lưu vị trí</button>
     <button type="button" class="a" onclick="LOC.edit=null;$('#locform').innerHTML=locFormHtml()">Nhập mới</button></div>`;
}
async function locList() {
  const d = await api(W(`/api/locations/search?q=${encodeURIComponent(LOC.q)}&wh=${encodeURIComponent(LOC.wh)}&page=${LOC.page}&size=${LOC.size}`));
  LOC.rows = d.rows;
  const pages = Math.max(1, Math.ceil(d.total / d.size));
  $('#loccount').textContent = `${fmt(d.total)} vị trí · trang ${d.page}/${pages}`;
  $('#loclist').innerHTML = table(d.rows, [['Location Code', 'code'], ['Kho', 'zone'], ['Node', 'node'], ['Level Height', r => esc(r.hb?.level_height), 'num'],
    ['Toạ độ', r => esc(r.hb?.coord)], ['Loại', r => esc(r.hb?.business_type)],
    ['Trạng thái', r => badge(r.state) + (r.removed ? ' <span class="b warn">đã bỏ khỏi file</span>' : '')], ['Pallet', 'pallet_id'],
    ['', r => `<button type="button" class="a" onclick="locEdit('${esc(r.code)}')">Sửa</button> <button type="button" class="a d" onclick="locDel('${esc(r.code)}', '${esc(r.pallet_id || '')}')">Xoá</button>`]]) +
    `<div class="row" style="margin-top:8px"><button type="button" class="a" ${d.page <= 1 ? 'disabled' : ''} onclick="LOC.page--;locList()">‹ Trước</button>
     <button type="button" class="a" ${d.page >= pages ? 'disabled' : ''} onclick="LOC.page++;locList()">Sau ›</button></div>`;
}
function locAfter() { $('#locform').innerHTML = locFormHtml(); locList(); }
function locEdit(code) { const r = LOC.rows.find(x => x.code === code); LOC.edit = {...(r.hb || {}), code}; $('#locform').innerHTML = locFormHtml(); $('#locform').scrollIntoView({behavior: 'smooth'}); }
async function locSave() {
  const row = Object.fromEntries($$('#hbf [data-h]').map(el => [el.dataset.h, el.value.trim() === '' ? null : el.value.trim()]));
  const r = done(await api(W('/api/locations/row'), {row, old_code: LOC.edit?.code || ''}));
  if (r.ok) { LOC.edit = null; locAfter(); }
}
async function locDel(code, pallet) {
  if (!await ask('Xoá vị trí', `Xoá <b>${esc(code)}</b> khỏi danh sách vị trí?` + (pallet ? `<br>Pallet <b>${esc(pallet)}</b> đang ở ô này cũng bị xoá theo.` : ''), 'Xoá', 'd')) return;
  done(await api(W('/api/locations/delete'), {codes: [code]})); locList();
}
async function locBulk() {
  const b = Object.fromEntries($$('#bulk [data-b]').map(el => [el.dataset.b, el.value.trim()]));
  const r = done(await api(W('/api/locations/bulk'), b)); if (r.ok) locList();
}
async function locReload() { const r = await api(W('/api/locations/reload'), {}); toast(r.ok ? `Đã nạp ${r.total} vị trí (${r.added} mới, xoá ${r.removed})` : r, !r.ok); locList(); }

// --- nạp file: đọc -> chọn kho / số điểm -> khác biệt -> áp dụng
async function impRead() {
  const f = $('#locfile').files[0]; if (!f) return toast('Chọn file trước', true);
  const b64 = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
  const r = await api(W('/api/locations/import/preview'), {name: f.name, b64});
  if (!r.ok) return toast(r, true);
  LOC.imp = r;
  $('#impbox').innerHTML = `<h3 style="margin-top:14px">2. Chọn kho và số điểm (file ${esc(r.name)}: ${fmt(r.total)} điểm)</h3>` +
    table(r.groups, [['Kho (tiền tố)', g => `<b>${esc(g.wh)}</b>${g.exists ? '' : ' <span class="b run">kho mới</span>'}`], ['Điểm trong file', 'count', 'num'],
      ['Nạp', g => `<input type="checkbox" data-g="${esc(g.wh)}" ${IMPORT_DEFAULT[g.wh] ? 'checked' : ''}>`],
      ['Số điểm tối đa (0 = tất cả)', g => `<input type="number" min="0" data-lim="${esc(g.wh)}" value="${IMPORT_DEFAULT[g.wh] ?? 0}" style="width:110px">`]]) +
    `<div class="row" style="margin-top:10px"><button type="button" class="a" onclick="impDiff()">3. Xem khác biệt</button></div><div id="impdiff"></div>`;
}
function impSel() {
  return Object.fromEntries($$('#impbox [data-g]').filter(el => el.checked).map(el => [el.dataset.g, +$(`#impbox [data-lim="${el.dataset.g}"]`).value || 0]));
}
async function impDiff() {
  const d = await api(W('/api/locations/import/diff'), {token: LOC.imp.token, select: impSel()});
  if (!d.ok) return toast(d, true);
  const lst = a => a.length ? esc(a.slice(0, 30).join(', ')) + (a.length > 30 ? ` … (+${a.length - 30})` : '') : '–';
  $('#impdiff').innerHTML = `<div class="tiles" style="margin-top:12px">${tile('Sẽ quản lý', fmt(d.selected), 'điểm')}${tile('Thêm mới', fmt(d.added.length), '')}
    ${tile('Xoá', fmt(d.removed.length), '')}${tile('Giữ nguyên', fmt(d.kept), '')}${tile('Ô có hàng bị xoá', fmt(d.in_use.length), d.in_use.length ? '<span class="st crit"><i>▲</i>pallet bị xoá theo</span>' : '')}</div>
    <div class="hint"><b>Thêm:</b> ${lst(d.added)}<br><b>Xoá:</b> ${lst(d.removed)}${d.in_use.length ? '<br><b>Pallet bị xoá theo:</b> ' + esc(d.in_use.map(x => `${x.pallet_id || '?'} @ ${x.code}`).join(', ')) : ''}</div>
    <div class="row"><button type="button" class="p" onclick="impApply(${d.in_use.length})">4. Áp dụng</button></div>`;
}
async function impApply(inUse) {
  if (!await ask('Áp dụng danh sách vị trí mới', 'Thay danh sách vị trí đang quản lý bằng lựa chọn này?' + (inUse ? `<br><b>${inUse}</b> ô đang có hàng sẽ bị xoá cùng pallet.` : ''), 'Áp dụng', inUse ? 'd' : 'p')) return;
  const r = done(await api(W('/api/locations/import/apply'), {token: LOC.imp.token, select: impSel()}));
  if (r.ok) { LOC.imp = null; show(); }
}

// --- danh mục kho (sửa qua WMS ảo; WCS B dùng chung file)
async function warehousePage() {
  const whs = await api(W('/api/warehouses'));
  return `<div class="noauto"><div class="card"><h3>Kho (ghép điểm theo tiền tố)</h3>
   <div class="hint">Mỗi điểm thuộc kho có tiền tố khớp dài nhất (S02-… vào kho S02 trước kho S). Tiền tố nhiều giá trị cách nhau dấu phẩy.
   WH_Code: mã kho gửi trong bản tin TKELog. Robot: 1 nâng hạ / 2 đội gầm, dùng khi gọi robot vào kho này. "Cất TP": kho mặc định để cất pallet định danh xong.</div>
   ${editTable('wht', whs.map(w => ({...w, prefixes: w.prefixes.join(', ')})), [['code', 'Mã kho', 'str'], ['name', 'Tên kho', 'wide'], ['prefixes', 'Tiền tố điểm', 'str'],
     ['wh_code', 'WH_Code', 'str'], ['robot', 'Robot', 'sel', [1, 2]], ['store', 'Cất TP', 'bool'], ['active', 'Dùng', 'bool']])}
   <div class="hint" style="margin-top:6px">Robot: 1 = nâng hạ (xe nâng), 2 = đội gầm. Số điểm hiện tại: ${whs.map(w => `${esc(w.code)} ${w.count}`).join(' · ') || '–'}</div>
   <div class="row" style="margin-top:10px"><button type="button" class="p" onclick="whSave()">Lưu danh mục kho</button></div></div></div>`;
}
async function whSave() {
  const rows = readTable('wht').map(r => ({...r, prefixes: r.prefixes.split(',').map(s => s.trim()).filter(Boolean), robot: +r.robot || 1}));
  const r = done(await api(W('/api/warehouses'), {warehouses: rows}, 'PUT')); if (r.ok) show();
}
