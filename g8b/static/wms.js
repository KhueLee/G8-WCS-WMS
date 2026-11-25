// Trang của WMS ảo trong site chung (API qua /wms/...)
let CFG = {}, PRODUCTS = [];
const today = () => new Date().toLocaleDateString('en-GB');   // dd/mm/yyyy
const whName = c => (CFG.warehouses || []).find(w => w.code === c)?.name || c || '–';
const whOpts = (sel, all) => (all ? '<option value="">Tất cả kho</option>' : '') +
  (CFG.warehouses || []).filter(w => w.active !== false).map(w => `<option value="${esc(w.code)}" ${w.code === sel ? 'selected' : ''}>${esc(w.code)} – ${esc(w.name)}</option>`).join('');
const lineOpts = pre => { const ks = Object.keys(CFG.stations || {}); const f = ks.filter(k => k.startsWith(pre)); return (f.length ? f : ks).map(k => `<option>${esc(k)}</option>`).join(''); };
const prodOpts = sel => PRODUCTS.filter(p => p.active !== false).map(p => `<option value="${esc(p.item_code)}" ${p.item_code === sel ? 'selected' : ''}>${esc(p.item_code)} – ${esc(p.item_name)}</option>`).join('');
const PSTAT = {PLANNED: 'Chờ định danh', IDENTIFIED: 'Đã định danh, chờ cất', STORED: 'Trong kho', SHIPPED: 'Đã xuất hết', CANCELLED: 'Đã hủy'};
const KIND = {PO: 'Định danh (SX)', ASN: 'Định danh (TM)', MOVE: 'Luân chuyển', DO: 'Xuất hàng', UPDATE_LOC: 'Cập nhật vị trí'};

async function loadWmsCfg() {
  const [c, p] = await Promise.all([api(W('/api/config')), api(W('/api/products'))]);
  if (c && c.version) CFG = c;
  if (Array.isArray(p)) PRODUCTS = p;
}

async function post(path, body, after) {
  const r = await api(path, body);
  toast(r.ok ? (r.msg || `Đã tạo lệnh ${r.po_name || r.order_id || ''}`) : r, !r.ok);
  if (r.ok && after) await after(r);
  return r;
}
const fdata = e => { e.preventDefault(); return Object.fromEntries(new FormData(e.target).entries()); };
const fileB64 = f => new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
const dlBtn = (href, label, kind = 'a') => `<a class="${kind}" href="${href}" style="text-decoration:none;display:inline-block;padding:6px 12px">${label}</a>`;

// ---------------------------------------------------------------- lệnh sản xuất
const PS = {status: 'WAITING,IDENTIFYING', q: '', timer: null, imp: null};
const PSTATUS = () => CFG.prod_status || {WAITING: 'Chờ định danh', IDENTIFYING: 'Đang định danh', DONE: 'Hoàn thành', CANCELLED: 'Đã hủy'};
const PBADGE = {WAITING: 'warn', IDENTIFYING: 'run', DONE: 'ok', CANCELLED: ''};
const pbadge = s => `<span class="b ${PBADGE[s] || ''}">${esc(PSTATUS()[s] || s)}</span>`;
function progressCell(r) {
  const g = r.progress || {};
  if (!g.ctn_plan) return r.status === 'WAITING' ? '<span class="hint">chưa định danh</span>' : '';
  return `<div class="prog" title="${g.pct}%"><div style="width:${Math.min(100, g.pct)}%"></div></div>
    <div class="hint" style="margin:2px 0 0">${fmt(g.ctn_done)}/${fmt(g.ctn_plan)} thùng · ${g.pallets_done}/${g.pallets} pallet · đã cất ${g.pallets_stored}${g.pallets_cancelled ? ` · hủy ${g.pallets_cancelled}` : ''}</div>`;
}
async function prodList() {
  if (CUR !== 'w_prod') { clearInterval(PS.timer); PS.timer = null; return; }
  const rows = await api(W('/api/prod?' + new URLSearchParams({status: PS.status, q: PS.q})));
  const el = $('#prodlist'); if (!el) return;
  $('#prodcount').textContent = `${rows.length} lệnh`;
  el.innerHTML = table(rows, [['Lệnh SX', r => `<b style="white-space:nowrap">${esc(r.code)}</b>`], ['Ngày SX', 'prod_date'], ['Line', 'line'],
    ['Sản phẩm', r => `${esc(r.item_code)}${r.product_missing ? ' <span class="b bad">chưa có SP</span>' : !r.product_ok ? ' <span class="b warn">thiếu quy cách</span>' : ''}<div class="hint" style="margin:0">${esc(r.item_name || '')}</div>`],
    ['Số kiện', 'ctn', 'num'], ['Tổng SL', 'qty_total', 'num'], ['Kho nhập TP', 'wh_in'],
    ['Trạng thái', r => pbadge(r.status) + (r.identify?.line_id ? `<div class="hint" style="margin:0">bàn ${esc(r.identify.line_id)}</div>` : '')],
    ['Tiến độ định danh', progressCell], ['Ghi chú', 'note'],
    ['', r => (r.status === 'WAITING' ? `<button type="button" class="p" onclick="prodStart('${esc(r.key)}')">Định danh</button> ` : '')
      + (r.status === 'IDENTIFYING' ? `<button type="button" class="a d" onclick="prodCancel('${esc(r.key)}')">Hủy định danh</button> ` : '')
      + `<button type="button" class="a" onclick="prodEditDlg('${esc(r.key)}')">Sửa</button>`
      + (r.status !== 'IDENTIFYING' ? ` <button type="button" class="a d" onclick="prodDelete('${esc(r.key)}')">Xoá</button>` : '')]]);
  PS.rows = rows;
}
function prodAfter() { prodList(); clearInterval(PS.timer); PS.timer = setInterval(() => { if (!$('#dlg.show')) prodList(); }, 3000); }

async function prodStart(key) {
  const o = PS.rows.find(r => r.key === key), p = PRODUCTS.find(x => x.item_code === o.item_code);
  if (!p) return toast(`Sản phẩm ${o.item_code} chưa có trong danh mục (Danh mục → Sản phẩm)`, true);
  if (!(p.inner_carton > 0 && p.inner_pallet > 0)) return toast(`Sản phẩm ${o.item_code} chưa có quy cách thùng / pallet – nhập ở Danh mục → Sản phẩm`, true);
  const desks = Object.keys(CFG.stations || {}).filter(k => !k.startsWith('BCXB'));
  if (!desks.length) return toast('Chưa khai bàn định danh (Hệ thống → Cấu hình WMS → Line → điểm sàn)', true);
  const def = desks.includes(o.line_id) ? o.line_id : desks[0];
  const pal = Math.ceil(o.ctn / p.inner_pallet);
  const r = await dialog({title: `Định danh lệnh ${o.code}`, body: `<p><b>${esc(o.item_code)}</b> – ${esc(p.item_name)}<br>
    Quy cách: ${p.inner_carton} chiếc/thùng · ${p.inner_pallet} thùng/pallet. Line trên lệnh: ${esc(o.line)}</p>
    <div class="row"><label class="fl">Bàn định danh<select name="line_id">${desks.map(d => `<option ${d === def ? 'selected' : ''}>${esc(d)}</option>`).join('')}</select></label>
    <label class="fl">Số thùng định danh<input name="ctn" type="number" min="1" value="${o.ctn}" style="width:120px"></label>
    <label class="fl">NSX<input name="mfg_date" value="${esc(o.prod_date || today())}" style="width:120px"></label>
    <label class="fl">Kho cất<select name="dest_wh">${whOpts(CFG.store_zone)}</select></label></div>
    <p class="hint">${o.ctn} thùng ≈ ${pal} pallet. WMS sinh mã thùng, giữ chỗ ${pal} ô trống trong kho cất và gửi lệnh xuống bàn định danh.
    Pallet nào đủ thùng: bàn định danh báo về, robot tự cất vào ô đã giữ.</p>`,
    buttons: [{label: 'Thôi', value: null}, {label: 'Bắt đầu định danh', value: 'go', kind: 'p'}]});
  if (r.value !== 'go') return;
  await post(W('/api/prod/start'), {key, ...r.data, ctn: +r.data.ctn}, prodList);
}
async function prodCancel(key) {
  const o = PS.rows.find(r => r.key === key);
  if (await ask('Hủy định danh', `Hủy định danh lệnh <b>${esc(o.code)}</b>? Bàn định danh làm nốt pallet đang dở, các pallet chưa làm bị hủy và nhả ô đã giữ. Lệnh chuyển sang "Đã hủy".`, 'Hủy định danh', 'd'))
    await post(W('/api/prod/cancel'), {key}, prodList);
}
async function prodDelete(key) {
  const o = PS.rows.find(r => r.key === key);
  if (await ask('Xoá lệnh sản xuất', `Xoá lệnh <b>${esc(o.code)}</b> (line ${esc(o.line)})?`, 'Xoá', 'd')) await post(W('/api/prod/delete'), {key}, prodList);
}
async function prodEditDlg(key) {
  const o = key ? PS.rows.find(r => r.key === key) : {status: 'WAITING', prod_date: today(), type: 'Thành Phẩm', wh_in: 'KTM001RB', wh_out: 'KVT002'};
  const busy = o.status === 'IDENTIFYING';
  const f = (n, l, t = 'text', w = 130) => `<label class="fl">${l}<input name="${n}" type="${t}" value="${esc(o[n] ?? '')}" style="width:${w}px" ${busy && n !== 'note' ? 'disabled' : ''}></label>`;
  const st = Object.entries(PSTATUS()).filter(([k]) => k !== 'IDENTIFYING');
  const r = await dialog({title: key ? `Sửa lệnh ${o.code}` : 'Thêm lệnh sản xuất', body: `
    ${busy ? '<p class="hint">Lệnh đang định danh: chỉ sửa được ghi chú.</p>' : ''}
    <div class="row">${f('code', 'Lệnh sản xuất', 'text', 150)}${f('line', 'Line', 'text', 130)}${f('prod_date', 'Ngày SX (dd/mm/yyyy)', 'text', 120)}</div>
    <div class="row"><label class="fl">Mã SP Group<input name="item_code" list="prodcodes" value="${esc(o.item_code || '')}" style="width:220px" ${busy ? 'disabled' : ''}></label>
      <datalist id="prodcodes">${PRODUCTS.map(p => `<option value="${esc(p.item_code)}">${esc(p.item_name)}</option>`).join('')}</datalist>
      ${f('ctn', 'Số kiện', 'number', 100)}${f('qty_total', 'Tổng SL', 'number', 110)}</div>
    <div class="row">${f('wh_in', 'Kho nhập TP')}${f('wh_out', 'Kho xuất')}${f('type', 'Loại hình SX')}
      ${busy ? '' : `<label class="fl">Trạng thái<select name="status">${st.map(([k, v]) => `<option value="${k}" ${k === o.status ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>`}</div>
    <div class="row"><label class="fl" style="flex:1">Ghi chú<input name="note" value="${esc(o.note || '')}" style="width:100%"></label></div>`,
    buttons: [{label: 'Thôi', value: null}, {label: 'Lưu', value: 'save', kind: 'p'}]});
  if (r.value !== 'save') return;
  const b = {...o, ...r.data, old_key: key || ''};
  if (!b.qty_even && b.qty_total) b.qty_even = b.qty_total;
  await post(W('/api/prod'), b, prodList);
}
async function prodImpRead() {
  const f = $('#prodfile').files[0]; if (!f) return toast('Chọn file trước', true);
  const r = await api(W('/api/prod/import/preview'), {name: f.name, b64: await fileB64(f)});
  if (!r.ok) return toast(r, true);
  PS.imp = r;
  $('#prodimp').innerHTML = `<h3 style="margin-top:12px">2. Chọn trạng thái cần nạp (file ${esc(r.name)}: ${fmt(r.total)} dòng)</h3>` +
    table(r.groups, [['Trạng thái trong file', g => `<b>${esc(g.status_text)}</b>`], ['Số dòng', 'count', 'num'], ['Mới', 'new', 'num'],
      ['Đã có, đang chờ (cập nhật)', 'exists', 'num'], ['Đã có, đang định danh / xong (giữ nguyên)', 'busy', 'num'],
      ['Nạp', g => `<input type="checkbox" data-st="${esc(g.status_text)}" ${g.waiting ? 'checked' : ''}>`]]) +
    (r.missing_products.length ? `<p class="hint"><span class="st warn"><i>▲</i></span> Sản phẩm chưa có trong danh mục: ${esc(r.missing_products.join(', '))} – nạp file sản phẩm hoặc thêm tay trước khi định danh.</p>` : '') +
    `<div class="row" style="margin-top:8px"><button type="button" class="p" onclick="prodImpApply()">3. Nạp vào – trạng thái Chờ định danh</button></div>`;
}
async function prodImpApply() {
  const statuses = $$('#prodimp [data-st]').filter(x => x.checked).map(x => x.dataset.st);
  if (!statuses.length) return toast('Chọn ít nhất 1 trạng thái', true);
  const r = done(await api(W('/api/prod/import/apply'), {token: PS.imp.token, statuses}));
  if (r.ok) { PS.imp = null; $('#prodimp').innerHTML = ''; $('#prodfile').value = ''; await loadWmsCfg(); prodList(); }
}

async function prodsImpRead() {
  const f = $('#prodsfile').files[0]; if (!f) return toast('Chọn file trước', true);
  const r = await api(W('/api/products/import/preview'), {name: f.name, b64: await fileB64(f)});
  if (!r.ok) return toast(r, true);
  $('#prodsimp').innerHTML = `<div class="tiles" style="margin-top:12px">${tile('Sản phẩm trong file', fmt(r.total), `${fmt(r.lines)} dòng mã – chuyền`)}
    ${tile('Thêm mới', fmt(r.new), '')}${tile('Cập nhật', fmt(r.update), 'tên, chuyền')}${tile('Có quy cách thùng', fmt(r.with_spec), 'lấy từ tên "(..c/T)" hoặc cột quy cách')}</div>
    <p class="hint">Sản phẩm đã có: cập nhật tên và chuyền; quy cách đang có được giữ (trừ khi file có cột quy cách). Sản phẩm mới chưa có quy cách pallet: nhập ở bảng dưới trước khi định danh.</p>
    <button type="button" class="p" onclick="prodsImpApply('${r.token}')">2. Nạp vào danh mục</button>`;
}
async function prodsImpApply(token) {
  const r = done(await api(W('/api/products/import/apply'), {token}));
  if (r.ok) { await loadWmsCfg(); show(); }
}

// ---------------------------------------------------------------- định danh
function idSpec() {
  const p = PRODUCTS.find(x => x.item_code === $('#id_item')?.value), n = +$('#id_ctn')?.value || 0;
  if (!p) return;
  const pal = Math.ceil(n / p.inner_pallet), last = n - (pal - 1) * p.inner_pallet;
  $('#id_spec').innerHTML = `Quy cách: <b>${p.inner_carton}</b> chiếc/thùng · <b>${p.inner_pallet}</b> thùng/pallet · GW ${p.gw} kg/thùng →
    <b>${pal}</b> pallet${pal > 1 && last !== p.inner_pallet ? ` (pallet cuối ${last} thùng)` : ''}, ${fmt(n * p.inner_carton)} chiếc`;
}
async function newIdentify(e) { const f = fdata(e); await post(W('/api/identify'), {...f, ctn_total: +f.ctn_total}, show); }
async function cancelPo(po, name) {
  if (await ask('Hủy lệnh định danh', `Hủy lệnh <b>${esc(name || po)}</b>? Bàn định danh làm nốt pallet đang dở, các pallet chưa làm bị hủy.`, 'Hủy lệnh', 'd'))
    await post(W('/api/identify/cancel'), {po_id: po}, show);
}

// ---------------------------------------------------------------- luân chuyển / cập nhật vị trí
async function cellOpts(selId, wh, onlyEmpty, keep) {
  const cells = await api(W('/api/locations?wh=' + encodeURIComponent(wh)));
  const ok = cells.filter(c => c.kind === 'RACK' && (!onlyEmpty || (c.state === 'EMPTY' && !c.task)));
  $(selId).innerHTML = (keep || '') + ok.map(c => `<option value="${esc(c.code)}">${esc(c.code)}${c.state !== 'EMPTY' ? ' · ' + c.state + ' ' + esc(c.pallet_id || '') : ''}</option>`).join('');
}
async function newMove(e) { const f = fdata(e); await post(W('/api/move'), {pallet_id: f.pallet_id, to: f.to, type_move: +f.type_move, type_robot: +f.type_robot}, show); }
async function newUpdLoc(e) { const f = fdata(e); await post(W('/api/update_location'), f, show); }

// ---------------------------------------------------------------- xuất
async function newExport(e) {
  const f = fdata(e);
  const picks = $$('#dop tbody tr').filter(tr => $('input[type=checkbox]', tr)?.checked)
    .map(tr => ({pallet_id: tr.dataset.p, ctn_need: +$('input[type=number]', tr).value}));
  if (!picks.length) return toast('Chọn ít nhất 1 pallet', true);
  await post(W('/api/export'), {belt: f.belt, picks}, show);
}

// ---------------------------------------------------------------- pallet / ô / sản phẩm: sửa tay bằng hộp thoại lựa chọn
async function emptyCellOptions(current) {
  const cells = (await api(W('/api/locations'))).filter(c => c.kind === 'RACK' && ((c.state === 'EMPTY' && !c.task) || c.code === current));
  const byWh = {};
  cells.forEach(c => (byWh[c.zone] = byWh[c.zone] || []).push(c));
  return `<option value="">(bỏ khỏi kệ – không vị trí)</option>` + Object.entries(byWh).map(([z, cs]) =>
    `<optgroup label="${esc(z)} – ${esc(whName(z))}">${cs.map(c => `<option value="${esc(c.code)}" ${c.code === current ? 'selected' : ''}>${esc(c.code)}${c.code === current ? ' (hiện tại)' : ''}</option>`).join('')}</optgroup>`).join('');
}
async function palEdit(pid) {
  const p = await api(W('/api/pallets/' + encodeURIComponent(pid)));
  const r = await dialog({title: `Sửa pallet ${pid}`, body: `<p>${esc(p.item_code)} · ${p.cartons?.length || 0} thùng · NSX ${esc(p.mfg_date || '–')}</p>
    <div class="row"><label class="fl">Trạng thái<select name="status">${Object.entries(PSTAT).map(([k, v]) => `<option value="${k}" ${k === p.status ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label class="fl">Vị trí<select name="location">${await emptyCellOptions(p.location || '')}</select></label></div>
    <p class="hint">Đổi vị trí ở đây = cập nhật vị trí tay (không gọi robot).</p>`,
    buttons: [{label: 'Thôi', value: null}, {label: 'Lưu', value: 'save', kind: 'p'}]});
  if (r.value === 'save') await post(W('/api/pallets/update'), {pallet_id: pid, location: r.data.location, status: r.data.status}, show);
}
async function palDel(pid) { if (await ask('Xoá pallet', `Xoá pallet <b>${esc(pid)}</b>? Ô đang chứa / giữ chỗ cho pallet được nhả.`, 'Xoá', 'd')) await post(W('/api/pallets/delete'), {pallet_id: pid}, show); }
async function palCreate(e) { const f = fdata(e); await post(W('/api/pallets/create'), {...f, ctn: +f.ctn}, show); }

async function cellSet(code) {
  const c = (await api(W('/api/locations'))).find(x => x.code === code);
  if (!c) return;
  const btns = [{label: 'Đóng', value: null}];
  if (c.state === 'LOCKED') btns.push({label: 'Mở khoá', value: 'EMPTY', kind: 'p'});
  if (c.state === 'EMPTY' && !c.task) {
    btns.push({label: 'Khoá ô', value: 'LOCKED', kind: 'd'});
    btns.push({label: 'Đặt pallet vào ô…', value: 'place', kind: 'p'});
  }
  if (c.state === 'FULL') btns.push({label: 'Làm trống ô (pallet mất vị trí)', value: 'EMPTY', kind: 'd'});
  const desc = {EMPTY: c.task ? 'Trống – đang giữ chỗ cho ' + c.task.replace('reserve:', '') : 'Trống', FULL: 'Có pallet', BUSY: 'Robot đang chở tới – không sửa được', LOCKED: 'Đang khoá'}[c.state];
  const r = await dialog({title: `Ô ${code}`, body: `<p>Kho <b>${esc(c.zone)}</b> – ${esc(whName(c.zone))}<br>Trạng thái: ${badge(c.state)} ${esc(desc)}${c.pallet_id ? `<br>Pallet: <b>${esc(c.pallet_id)}</b>` : ''}</p>`, buttons: btns});
  if (!r.value) return;
  if (r.value === 'place') {
    const ps = await api(W('/api/pallets?status=PLANNED,IDENTIFIED,STORED'));
    const r2 = await dialog({title: `Đặt pallet vào ô ${code}`, body: `<label class="fl">Pallet<select name="pallet_id">${ps.map(p => `<option value="${esc(p.pallet_id)}">${esc(p.pallet_id)} · ${esc(p.item_code)} @ ${esc(p.location || '(không vị trí)')}</option>`).join('')}</select></label>
      <p class="hint">Cập nhật vị trí tay, không gọi robot.</p>`, buttons: [{label: 'Thôi', value: null}, {label: 'Đặt vào ô', value: 'ok', kind: 'p'}]});
    if (r2.value === 'ok') await post(W('/api/update_location'), {pallet_id: r2.data.pallet_id, to: code, note: 'đặt từ ô kệ'}, show);
    return;
  }
  await post(W('/api/locations/set'), {code, state: r.value}, show);
}

let PEDIT = null;
function prodForm() {
  const p = PEDIT || {inner_carton: '', inner_pallet: '', gw: '', nw: 0, active: true};
  return `<h3>${PEDIT ? 'Sửa sản phẩm ' + esc(PEDIT.item_code) : 'Thêm sản phẩm'}</h3><form class="f" onsubmit="prodSave(event)">
   <label class="fl">Mã SP<input name="item_code" value="${esc(p.item_code || '')}" required></label>
   <label class="fl">Tên SP<input name="item_name" value="${esc(p.item_name || '')}" required style="min-width:300px"></label>
   <label class="fl">Nhóm<input name="group" value="${esc(p.group || '')}"></label>
   <label class="fl">Chuyền (cách nhau dấu phẩy)<input name="lines" value="${esc((p.lines || []).join(', '))}" style="min-width:220px"></label>
   <label class="fl">Quy cách thùng (chiếc/thùng)<input name="inner_carton" type="number" min="1" value="${esc(p.inner_carton)}" required></label>
   <label class="fl">Quy cách pallet (thùng/pallet)<input name="inner_pallet" type="number" min="1" value="${esc(p.inner_pallet)}" required></label>
   <label class="fl">GW kg/thùng<input name="gw" type="number" step="any" value="${esc(p.gw)}"></label>
   <label class="fl">NW kg/thùng<input name="nw" type="number" step="any" value="${esc(p.nw)}"></label>
   <label class="ck"><input type="checkbox" name="active" ${p.active !== false ? 'checked' : ''}>Dùng</label>
   <button class="p">Lưu</button> <button type="button" class="a" onclick="PEDIT=null;show()">Nhập mới</button></form>`;
}
async function prodSave(e) {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  f.active = !!e.target.active.checked; f.old_code = PEDIT?.item_code || '';
  await post(W('/api/products'), f, async () => { PEDIT = null; await loadWmsCfg(); show(); });
}
function prodEdit(code) { PEDIT = PRODUCTS.find(p => p.item_code === code); show().then(() => $('#main form.f')?.scrollIntoView({behavior: 'smooth'})); }
async function prodDel(code) { if (await ask('Xoá sản phẩm', `Xoá sản phẩm <b>${esc(code)}</b>?`, 'Xoá', 'd')) await post(W('/api/products/delete'), {item_code: code}, async () => { await loadWmsCfg(); show(); }); }

async function saveWmsCfg() {
  const c = readFields($('#cfg'), {});
  c.users = Object.fromEntries(readTable('ut').filter(r => r.user).map(r => [r.user, r.password]));
  c.stations = Object.fromEntries(readTable('stt').filter(r => r.line_id).map(r => [r.line_id, r.location]));
  const r = done(await api(W('/api/settings'), c, 'PUT'));
  if (r.ok) { await loadWmsCfg(); show(); }
}

const PF = {wh: '', status: 'PLANNED,IDENTIFIED,STORED', q: ''};
const LF = {wh: ''};

// ---------------------------------------------------------------- đăng ký trang
page('w_dash', 'overview', 'Kho (WMS)', 'WMS', async () => {
  await loadWmsCfg();
  const d = await api(W('/api/dashboard')), k = d.tiles, p = k.pallets || {};
  const byKind = Object.entries(k.orders_by_kind || {}).map(([a, b]) => `${KIND[a] || a} ${b}`).join(' · ') || 'chưa có lệnh';
  return `<div class="tiles">
    ${tile('Lệnh hôm nay', fmt(k.orders_today), esc(byKind))}
    ${tile('Lệnh đang chạy', fmt(k.orders_running), '')}
    ${tile('Lệnh lỗi hôm nay', fmt(k.orders_failed), k.orders_failed ? '<span class="st crit"><i>▲</i>xem Theo dõi kho → Lệnh WMS</span>' : 'không có')}
    ${tile('Pallet trong kho', fmt(p.STORED || 0), `${fmt(k.cartons_in_stock)} thùng · ${fmt(p.IDENTIFIED || 0)} chờ cất · ${fmt(p.PLANNED || 0)} chờ định danh`)}
    ${tile(`Ô trống kho ${esc(d.store_zone)}`, fmt(k.zone_free), `${fmt(k.zone_full)}/${fmt(k.zone_total)} ô có hàng · ${fmt(k.zone_reserved)} đã giữ chỗ`)}
    ${tile('Bản tin chờ gửi WCS', fmt(k.outbox_pending), k.outbox_dead ? `<span class="st crit"><i>▲</i>${k.outbox_dead} bị từ chối</span>` : 'không có bản tin lỗi')}
  </div>
  <div class="grid2">
    <div class="card"><h3>Lệnh tạo theo giờ – hôm nay</h3>${hourBars(d.hourly_orders, 'lệnh')}</div>
    <div class="card"><h3>Pallet định danh xong theo giờ – hôm nay</h3>${hourBars(d.hourly_identified, 'pallet')}</div>
  </div>
  <div class="grid2">
    <div class="card"><h3>Vị trí theo kho</h3>${occupancy(d.occupancy)}</div>
    <div class="card"><h3>Tồn kho theo kho × sản phẩm</h3>${table(d.inventory, [['Kho', r => esc(r.wh) + ' · ' + esc(whName(r.wh))], ['Sản phẩm', 'item_code'], ['Pallet', 'pallets', 'num'], ['Thùng', 'cartons', 'num']])}</div>
  </div>
  <div class="card"><h3>Sự kiện gần đây</h3>${table(d.events, [['Giờ', 't'], ['Sự kiện', 'text']])}</div>`;
});

page('w_prod', 'order', 'Lệnh sản xuất', 'WMS', async () => {
  await loadWmsCfg();
  const st = PSTATUS();
  return `<div class="noauto"><div class="card"><h3>Lệnh sản xuất</h3>
   <div class="hint">Nạp file lệnh sản xuất xuất từ hệ thống G8 (CKHC_G8_San_Xuat_Export_*.xlsx): lệnh <b>Kế hoạch / Đang sản xuất</b> vào trạng thái <b>Chờ định danh</b>.
   Khi chuyền bắt đầu đóng thùng, người vận hành bấm <b>Định danh</b> trên lệnh: WMS sinh mã thùng, chia pallet, giữ chỗ ô cất và gửi lệnh xuống bàn định danh.
   Cột <b>Tiến độ</b> tự cập nhật theo pallet bàn định danh báo xong; đủ pallet thì lệnh chuyển <b>Hoàn thành</b>.</div>
   <div class="row"><label class="fl">File lệnh sản xuất (.xlsx)<input type="file" id="prodfile" accept=".xlsx"></label>
    <button type="button" class="a" onclick="prodImpRead()">1. Đọc file</button>
    ${dlBtn(W('/api/prod/export?status=' + encodeURIComponent(PS.status)), 'Xuất file (theo bộ lọc)')}
    ${dlBtn(W('/api/prod/export?status=WAITING'), 'Xuất lệnh chờ định danh')}
    <button type="button" class="p" onclick="prodEditDlg('')">Thêm lệnh tay</button></div>
   <div id="prodimp"></div></div>
  <div class="card"><div class="row" style="margin-bottom:10px">
   <label class="fl">Trạng thái<select onchange="PS.status=this.value;prodList()">${[['WAITING,IDENTIFYING', 'Chờ + đang định danh'], ...Object.entries(st), ['', 'Tất cả']]
     .map(([v, n]) => `<option value="${v}" ${PS.status === v ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
   <label class="fl">Tìm (mã lệnh / SP / line / ghi chú)<input value="${esc(PS.q)}" oninput="PS.q=this.value;prodList()"></label>
   <span class="hint" id="prodcount"></span></div><div id="prodlist"></div></div></div>`;
}, prodAfter);

page('w_new_id', 'order', 'Định danh', 'WMS', async () => {
  await loadWmsCfg();
  const orders = (await api(W('/api/orders'))).filter(o => ['PO', 'ASN'].includes(o.kind) && ['SENT', 'RUNNING'].includes(o.status));
  return `<div class="card"><h3>Lệnh định danh</h3>
  <div class="hint">Đi đôi với lệnh sản xuất: WMS sinh mã thùng (Carton_List) theo quy cách sản phẩm, chia pallet theo quy cách pallet,
  giữ chỗ ô trống trong kho cất cho từng pallet rồi gửi bàn định danh in / quét. Pallet định danh xong: WMS trả vị trí cất và gọi robot cất (nếu bật).</div>
  <form class="f" onsubmit="newIdentify(event)">
   <label class="fl">Loại<select name="kind"><option value="PO">PO – thành phẩm</option><option value="ASN">ASN – hàng thương mại</option></select></label>
   <label class="fl">Line (bàn định danh)<select name="line_id">${lineOpts('MCSX')}</select></label>
   <label class="fl">Sản phẩm<select name="item_code" id="id_item" onchange="idSpec()" style="min-width:280px">${prodOpts()}</select></label>
   <label class="fl">Số thùng kế hoạch<input name="ctn_total" id="id_ctn" type="number" min="1" value="${PRODUCTS[0]?.inner_pallet || 1}" oninput="idSpec()" required></label>
   <label class="fl">NSX (dd/mm/yyyy)<input name="mfg_date" value="${today()}"></label>
   <label class="fl">Kho cất<select name="dest_wh">${whOpts(CFG.store_zone)}</select></label>
   <label class="fl">Tên lệnh (trống = tự sinh VAS-ddmmyy-n)<input name="po_name"></label>
   <button class="p">Gửi lệnh</button></form><div class="hint" id="id_spec" style="margin-top:8px"></div></div>
  <div class="card"><h3>Lệnh định danh đang chạy</h3>${table(orders, [['Tên lệnh', 'po_name'], ['ID', 'po_id'], ['Loại', 'kind'], ['Line', 'line_id'], ['Sản phẩm', 'item_code'],
    ['Thùng', 'ctn_total', 'num'], ['Kho cất', 'dest_wh'], ['TT', r => badge(r.status)], ['Pallet xong', r => (r.done || []).length + '/' + (r.pallets || []).length, 'num'],
    ['', r => `<button class=a onclick="cancelPo(${r.po_id}, '${esc(r.po_name)}')">Hủy</button>`]])}</div>`;
}, idSpec);

page('w_new_mv', 'order', 'Luân chuyển pallet', 'WMS', async () => {
  await loadWmsCfg();
  const ps = await api(W('/api/pallets?status=STORED'));
  return `<div class="card"><h3>Luân chuyển pallet (gọi robot)</h3>
  <div class="hint">Chọn pallet đang trong kho và ô đích trống. Robot mặc định theo kho đích (1 nâng hạ / 2 đội gầm).</div>
  <form class="f" onsubmit="newMove(event)">
   <label class="fl">Pallet<select name="pallet_id" style="min-width:240px">${ps.map(p => `<option value="${esc(p.pallet_id)}">${esc(p.pallet_id)} · ${esc(p.item_code)} @ ${esc(p.location)}</option>`).join('')}</select></label>
   <label class="fl">Kho đích<select id="mv_wh" onchange="cellOpts('#mv_to', this.value, true)">${whOpts(CFG.store_zone)}</select></label>
   <label class="fl">Ô đích (trống)<select name="to" id="mv_to" style="min-width:140px"></select></label>
   <label class="fl">Type_Move<select name="type_move">${Object.entries(CFG.type_move || {}).map(([v, n]) => `<option value="${v}" ${v === '11' ? 'selected' : ''}>${v} – ${esc(n)}</option>`).join('')}</select></label>
   <label class="fl">Robot<select name="type_robot"><option value="0">Theo kho đích</option>${Object.entries(CFG.robots || {}).map(([v, n]) => `<option value="${v}">${v} – ${esc(n)}</option>`).join('')}</select></label>
   <button class="p">Gửi lệnh</button></form></div>`;
}, () => cellOpts('#mv_to', $('#mv_wh').value, true));

page('w_new_do', 'order', 'Xuất hàng', 'WMS', async () => {
  await loadWmsCfg();
  const ps = (await api(W('/api/pallets?status=STORED'))).filter(p => p.ctn_total > p.ctn_shipped);
  return `<div class="card"><h3>Xuất hàng lên băng tải</h3><div class="hint">Lấy từ pallet đã định danh trong kho. Tick pallet, sửa số thùng cần xuất (mặc định hết số còn lại).</div>
  <form class="f" onsubmit="newExport(event)" style="flex-direction:column;align-items:stretch">
   <div class="row"><label class="fl">Băng tải<select name=belt>${lineOpts('BCXB')}</select></label><button class="p">Gửi lệnh</button></div>
   <div class="tw"><table id="dop"><thead><tr><th></th><th>Pallet</th><th>Sản phẩm</th><th>Kho</th><th>Vị trí</th><th>NSX</th><th class="num">Còn</th><th>Cần xuất</th></tr></thead><tbody>
   ${ps.map(p => `<tr data-p="${esc(p.pallet_id)}"><td><input type="checkbox"></td><td>${esc(p.pallet_id)}</td><td>${esc(p.item_code)}</td><td>${esc(p.wh)}</td><td>${esc(p.location)}</td><td>${esc(p.mfg_date || '')}</td>
     <td class="num">${p.ctn_total - p.ctn_shipped}</td><td><input type="number" min="1" max="${p.ctn_total - p.ctn_shipped}" value="${p.ctn_total - p.ctn_shipped}" style="min-width:70px;width:80px"></td></tr>`).join('')
     || '<tr><td colspan=8 class="empty">Chưa có pallet trong kho</td></tr>'}</tbody></table></div></form></div>`;
});

page('w_new_ul', 'order', 'Cập nhật vị trí', 'WMS', async () => {
  await loadWmsCfg();
  const ps = await api(W('/api/pallets?status=PLANNED,IDENTIFIED,STORED'));
  return `<div class="card"><h3>Cập nhật vị trí (tay, không gọi robot)</h3>
  <div class="hint">Dùng khi pallet được dời bằng tay / kiểm kê thấy lệch: WMS ghi nhận vị trí mới, không gửi lệnh cho WCS.</div>
  <form class="f" onsubmit="newUpdLoc(event)">
   <label class="fl">Pallet<select name="pallet_id" style="min-width:240px">${ps.map(p => `<option value="${esc(p.pallet_id)}">${esc(p.pallet_id)} · ${esc(p.item_code)} @ ${esc(p.location || '–')}</option>`).join('')}</select></label>
   <label class="fl">Kho<select id="ul_wh" onchange="cellOpts('#ul_to', this.value, false, '<option value=&quot;&quot;>(bỏ khỏi kệ)</option>')">${whOpts(CFG.store_zone)}</select></label>
   <label class="fl">Ô mới<select name="to" id="ul_to" style="min-width:160px"></select></label>
   <label class="fl" style="flex:1">Ghi chú<input name="note" placeholder="vd. kiểm kê 25/11"></label>
   <button class="p">Cập nhật</button></form></div>`;
}, () => cellOpts('#ul_to', $('#ul_wh').value, false, '<option value="">(bỏ khỏi kệ)</option>'));

page('w_orders', 'track', 'Lệnh WMS', 'WMS', async () => table(await api(W('/api/orders')), [['Tạo', 'created_at'], ['Lệnh', 'order_id'], ['Loại', r => esc(KIND[r.kind] || r.kind)], ['Tên', 'po_name'], ['Line', 'line_id'],
  ['Trạng thái', r => badge(r.status)], ['Pallet', r => esc((r.pallets || [r.pallet_id]).join(', '))], ['Xong', r => esc((r.done || []).join(', '))],
  ['Chi tiết', r => esc(r.kind === 'UPDATE_LOC' ? `${r.from || '–'} → ${r.to || '–'} ${r.note || ''}` : r.kind === 'MOVE' ? (r.tasks || []).map(x => x.Location_Old + '→' + x.Location_New).join(', ') + ' ' + Object.values(r.results || {}).join(',') : (r.item_code ? `${r.item_code} ${r.ctn_total} thùng` : ''))],
  ['Lỗi', 'error']]));

page('w_pallets', 'track', 'Pallet / tồn kho', 'WMS', async () => {
  await loadWmsCfg();
  const ps = await api(W('/api/pallets?' + new URLSearchParams(PF).toString()));
  return `<div class="noauto"><div class="card"><h3>Tạo pallet tay</h3><div class="hint">Hàng có sẵn trong kho (chưa qua định danh của B): WMS sinh mã thùng theo quy cách để quản lý / xuất.</div>
   <form class="f" onsubmit="palCreate(event)">
    <label class="fl">Sản phẩm<select name="item_code" style="min-width:260px">${prodOpts()}</select></label>
    <label class="fl">Số thùng<input name="ctn" type="number" min="1" value="10"></label>
    <label class="fl">Ô (trống)<input name="location" placeholder="C24-1-1"></label>
    <label class="fl">Mã pallet (trống = tự sinh)<input name="pallet_id"></label>
    <label class="fl">NSX<input name="mfg_date" value="${today()}"></label><button class="p">Tạo</button></form></div>
  <div class="card"><div class="row" style="margin-bottom:10px">
   <label class="fl">Kho<select onchange="PF.wh=this.value;show()">${whOpts(PF.wh, true)}</select></label>
   <label class="fl">Trạng thái<select onchange="PF.status=this.value;show()">${[['PLANNED,IDENTIFIED,STORED', 'Đang quản lý'], ['STORED', 'Trong kho'], ['PLANNED', 'Chờ định danh'], ['IDENTIFIED', 'Chờ cất'], ['SHIPPED', 'Đã xuất'], ['CANCELLED', 'Đã hủy'], ['', 'Tất cả']]
     .map(([v, n]) => `<option value="${v}" ${PF.status === v ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
   <label class="fl">Tìm (pallet / SP / đầu mã ô)<input value="${esc(PF.q)}" onchange="PF.q=this.value;show()"></label><span class="hint">${ps.length} pallet</span></div>
  ${table(ps, [['Pallet', 'pallet_id'], ['Sản phẩm', 'item_code'], ['Trạng thái', r => badge(r.status) + ' ' + esc(PSTAT[r.status] || '')], ['Kho', 'wh'], ['Vị trí', 'location'],
    ['Ô đã gán', 'assigned_location'], ['NSX', 'mfg_date'], ['Thùng', 'ctn_total', 'num'], ['Đã xuất', 'ctn_shipped', 'num'], ['Lệnh', r => esc(r.po_name || r.order_id)],
    ['', r => `<button class=a onclick="palEdit('${esc(r.pallet_id)}')">Sửa</button> <button class="a d" onclick="palDel('${esc(r.pallet_id)}')">Xoá</button>`]])}</div></div>`;
});

page('w_locs', 'track', 'Vị trí', 'WMS', async () => {
  await loadWmsCfg();
  const rows = (await api(W('/api/locations?wh=' + encodeURIComponent(LF.wh)))).filter(l => l.kind === 'RACK');
  return `<div class="row" style="margin-bottom:10px"><label class="fl">Kho<select onchange="LF.wh=this.value;show()">${whOpts(LF.wh, true)}</select></label>
    <span class="hint">${rows.length} ô · bấm ô để xem và chọn thao tác (khoá / mở khoá / làm trống / đặt pallet).</span></div><div class=grid>` +
    rows.map(l => `<div class="cell ${l.state}" onclick="cellSet('${esc(l.code)}')" data-tip="${esc(l.zone)} · ${esc(l.state)}${l.pallet_id ? ' · pallet ' + esc(l.pallet_id) : ''} ${esc(l.task || '')}">${cellBody(l)}</div>`).join('') + '</div>';
});

page('w_events', 'track', 'Sự kiện', 'WMS', async () => table(await api(W('/api/events')), [['Giờ', 't'], ['Sự kiện', 'text']]));

page('w_products', 'catalog', 'Sản phẩm', 'WMS', async () => {
  await loadWmsCfg();
  const miss = PRODUCTS.filter(p => !(p.inner_carton > 0 && p.inner_pallet > 0)).length;
  return `<div class="noauto"><div class="card"><h3>Nhập / xuất file danh sách sản phẩm</h3>
   <div class="hint">Định dạng file "CKHC_G8_BOM_Linh_Kien_Chuyen_San_Xuat_Export_*.xlsx": cột Mã BOM | Tên BOM | Chuyền (một mã nhiều chuyền = nhiều dòng).
   File xuất ra giữ đúng 3 cột đó, thêm các cột quy cách phía sau (nạp lại được).</div>
   <div class="row"><label class="fl">File sản phẩm (.xlsx)<input type="file" id="prodsfile" accept=".xlsx"></label>
    <button type="button" class="a" onclick="prodsImpRead()">1. Đọc file</button> ${dlBtn(W('/api/products/export'), 'Xuất file danh sách sản phẩm')}</div>
   <div id="prodsimp"></div></div>
  <div class="card">${prodForm()}</div>
  <div class="card"><div class="row" style="margin-bottom:10px">
   <label class="fl">Tìm (mã / tên / chuyền)<input value="${esc(PRF.q)}" oninput="PRF.q=this.value;prodTable()"></label>
   <label class="ck"><input type="checkbox" ${PRF.miss ? 'checked' : ''} onchange="PRF.miss=this.checked;prodTable()">Chỉ sản phẩm thiếu quy cách</label>
   ${miss ? `<span class="hint"><span class="st warn"><i>▲</i>${miss} sản phẩm chưa đủ quy cách thùng / pallet</span> – bấm Sửa để nhập trước khi định danh.</span>` : ''}
   <span class="hint" id="prodtcount"></span></div><div id="prodtable"></div></div></div>`;
}, () => prodTable());
const PRF = {q: '', miss: false};
function prodTable() {
  const q = PRF.q.trim().toLowerCase();
  const rows = PRODUCTS.filter(p => (!PRF.miss || !(p.inner_carton > 0 && p.inner_pallet > 0)) &&
    (!q || [p.item_code, p.item_name, ...(p.lines || [])].some(x => String(x || '').toLowerCase().includes(q))));
  $('#prodtcount').textContent = `${rows.length}/${PRODUCTS.length} sản phẩm`;
  $('#prodtable').innerHTML = table(rows.slice(0, 300), [['Mã SP', 'item_code'], ['Tên', 'item_name'], ['Chuyền', r => esc((r.lines || []).join(', '))], ['Nhóm', 'group'],
    ['Chiếc/thùng', r => r.inner_carton > 0 ? r.inner_carton : '<span class="b warn">thiếu</span>', 'num'], ['Thùng/pallet', r => r.inner_pallet > 0 ? r.inner_pallet : '<span class="b warn">thiếu</span>', 'num'],
    ['GW kg/thùng', 'gw', 'num'], ['NW kg/thùng', 'nw', 'num'],
    ['Dùng', r => r.active !== false ? '✓' : ''], ['', r => `<button class=a onclick="prodEdit('${esc(r.item_code)}')">Sửa</button> <button class="a d" onclick="prodDel('${esc(r.item_code)}')">Xoá</button>`]])
    + (rows.length > 300 ? `<p class="hint">Hiện 300 dòng đầu – gõ ô tìm để lọc.</p>` : '');
}
page('w_wh', 'catalog', 'Kho', 'WMS', warehousePage);
page('w_loclist', 'catalog', 'Danh sách vị trí (highbay)', 'WMS', locationPage, locAfter);

page('w_outbox', 'system', 'Outbox WMS', 'WMS', async () => table(await api(W('/api/outbox')), [['Tạo', 'created_at'], ['Key', 'key'], ['Trạng thái', r => badge(r.status)], ['Lần', 'tries', 'num'], ['Lỗi cuối', 'last_error']]));
page('w_msg', 'system', 'Bản tin WMS', 'WMS', async () => msgTable(await api(W('/api/messages'))));
page('w_cfg', 'system', 'Cấu hình WMS', 'WMS', async () => {
  const S = await api(W('/api/settings')), c = S.config;
  return `<form id="cfg" onsubmit="return false"><div class="grid2">
   <div class="card"><h3>Kết nối WCS</h3><div class="row">${fld('URL WCS', 'wcs.url', c.wcs.url, 'str', 'style="min-width:260px"')}
    ${fld('Tự gọi robot cất kệ khi định danh xong', 'auto_putaway', c.auto_putaway, 'bool')}</div></div>
   <div class="card"><h3>Mã sinh tự động</h3><div class="row">${fld('Tiền tố tên lệnh', 'codes.po_prefix', c.codes.po_prefix)}
    ${fld('Tiền tố mã pallet', 'codes.pallet_prefix', c.codes.pallet_prefix)}${fld('Tiền tố mã thùng', 'codes.carton_prefix', c.codes.carton_prefix)}</div></div>
  </div>
  <div class="grid2">
   <div class="card"><h3>Tài khoản API cho WCS (TKELog Login)</h3>
    ${editTable('ut', Object.entries(c.users).map(([user, password]) => ({user, password})), [['user', 'User_Name', 'str'], ['password', 'Password', 'str']])}
    <div class="row" style="margin-top:10px">${fld('Token hết hạn sau (phút)', 'token_minutes', c.token_minutes, 'num')}</div></div>
   <div class="card"><h3>Line → điểm sàn</h3><div class="hint">Điểm robot lấy pallet ở bàn định danh / trả pallet ở băng tải.</div>
    ${editTable('stt', Object.entries(c.stations).map(([line_id, location]) => ({line_id, location})), [['line_id', 'Line_ID / Conveyor_Belt', 'str'], ['location', 'Điểm sàn', 'str']])}</div>
  </div>
  <div class="card"><h3>Khác</h3><div class="row">
    ${fld('Gửi lại sau (s)', 'outbox.retry_s', c.outbox.retry_s, 'num')}${fld('Timeout gửi (s)', 'outbox.timeout_s', c.outbox.timeout_s, 'num')}
    ${fld('Giữ bản tin (ngày)', 'messages_keep_days', c.messages_keep_days, 'num')}</div>
    <div class="hint" style="margin-top:10px">Cổng ${S.server.port}, MongoDB ${esc(S.mongo.uri)} / ${esc(S.mongo.db)}: sửa trong ${esc(S.file)} rồi chạy <b>run.bat stop</b> và <b>run.bat</b>.</div></div>
  <div class="savebar"><button type="button" class="p" onclick="saveWmsCfg()">Lưu và áp dụng</button><span class="hint">Ghi ${esc(S.file)} (giữ bản .bak), áp dụng ngay.</span></div>
  </form>`;
});
