// Trang của WCS B trong site chung (API qua /wcs/...)
const ACT = {cancel: ['Hủy task', 'Gửi lệnh hủy sang RCS (task đang chạy) hoặc hủy ngay (task đang chờ).', 'd'],
  complete: ['Hoàn thành tay', 'Đánh dấu task XONG mà không chờ RCS (dùng khi robot đã làm xong nhưng không báo). WCS báo WMS thành công.', 'p'],
  retry: ['Chạy lại', 'Chạy lại task lỗi và các task sau nó trong nhóm (orderId RCS mới).', 'p']};
async function act(action, task_id) {
  const [t, m, k] = ACT[action];
  if (!await ask(t, `${m}<br>Task: <b>${esc(task_id)}</b>`, t, k)) return;
  done(await api(C('/api/moves/action'), {action, task_id})); show();
}
async function lineEnable(line_id, enabled) {
  const msg = enabled ? 'WCS B nhận lại lệnh cho line này.' : 'WCS B từ chối lệnh mới cho line này (drain trước khi cutover). Lệnh đang chạy vẫn làm tiếp.';
  if (!await ask(`${enabled ? 'Bật' : 'Tắt'} line ${line_id}`, msg, enabled ? 'Bật' : 'Tắt', enabled ? 'p' : 'd')) return;
  done(await api(C('/api/lines/enable'), {line_id, enabled})); show();
}
async function wcsCell(code) {
  const c = (await api(C('/api/locations'))).find(x => x.code === code);
  if (!c) return;
  const btns = [{label: 'Đóng', value: null}];
  if (c.state === 'EMPTY') btns.push({label: 'Khoá ô', value: 'LOCKED', kind: 'd'}, {label: 'Đánh dấu có hàng', value: 'FULL', kind: 'p'});
  if (c.state === 'LOCKED') btns.push({label: 'Mở khoá', value: 'EMPTY', kind: 'p'});
  if (c.state === 'FULL') btns.push({label: 'Đánh dấu trống', value: 'EMPTY', kind: 'd'});
  const r = await dialog({title: `Ô ${code} (WCS B)`, body: `<p>Kho <b>${esc(c.zone)}</b> · ${badge(c.state)} ${c.state === 'BUSY' ? 'robot đang chở tới – không sửa được' : ''}${c.pallet_id ? `<br>Pallet: <b>${esc(c.pallet_id)}</b>` : ''}${c.task ? `<br>Task: ${esc(c.task)}` : ''}</p>
    <p class="hint">Trạng thái ô của WCS B dùng để giữ chỗ khi gọi robot. Tồn kho / pallet sửa ở WMS (Theo dõi kho → Ô kệ).</p>`, buttons: btns});
  if (r.value) { done(await api(C('/api/locations/set'), {code, state: r.value})); show(); }
}
async function obRetry(key) { done(await api(C('/api/outbox/retry'), {key})); show(); }
async function doClose(do_id) { if (await ask('Đóng tay DO', `Kết thúc DO <b>${esc(do_id)}</b> (xuất thiếu / hủy) để giải phóng băng tải?`, 'Đóng DO', 'd')) { done(await api(C('/api/export/close'), {do_id})); show(); } }

// ---------------------------------------------------------------- lệnh tay / bản tin test
async function orderMove(e) {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const steps = f.steps.split(/[,\n]/).map(s => s.trim()).filter(Boolean).map(s => { const [a, b] = s.split('>').map(x => x.trim()); return {from: a, to: b}; });
  const r = done(await api(C('/api/order/move'), {pallet_id: f.pallet_id, type_robot: +f.type_robot, type_move: +f.type_move, steps}));
  if (r.ok) e.target.reset();
}
const RAW_TPL = {
  move: {Task_ID: '1/1_MovePallet_test001', WH_Code: 'WH_TB', Pallet_ID: 'LPN00001', Type_Robot_ID: 1, Type_Move_ID: 11, Location_Old: 'BDD09', Location_New: 'C24-1-1'},
  identify: {PO_ID: 190999001, PO_Name: 'VAS-251126-99', WH_Code: 'WH_TB', Line_ID: 'MCSX009', Pallet_Current: 1, Item_Information: [{Item_Code: 'TRU_G01_50W_AT', Item_Name: 'Bóng Led trụ T50', MFG_Date: '25/11/2026', Pallet_ID: 'LPNT0001', Location: 'C24-1-1', Carton_List: ['TRU_G01_50W_AT|||||||6|0|TEST000001||001||||||||01', 'TRU_G01_50W_AT|||||||6|0|TEST000002||001||||||||02'], Ctn: 2, Qty: 12, Pcs: 0, Inner_Carton: 6, Inner_Pallet: 32, NW: 0, GW: 1.637}]},
  import: {ASN_ID: 180999001, WH_Code: 'WH_TB', Line_ID: 'MCSX009', Item_Information: [{Item_Code: 'HANG_TM', Item_Name: 'Hàng thương mại', Pallet_ID: 'LPNT0002', Location: 'C24-2-1', Carton_List: ['HANG_TM|||||||6|0|TEST000101||001||||||||01'], Ctn: 1, Qty: 6, Inner_Carton: 6, Inner_Pallet: 1, NW: 0, GW: 1}]},
  cancel: {PO_ID: 190999001},
  export: {DO_ID: 170999001, WH_Code: 'WH_TB', Conveyor_Belt: 'BCXB001', Item_Information: [{STT: 1, Pallet_ID: 'LPNT0001', Item_Code: 'TRU_G01_50W_AT', Ctn: 2, Ctn_Need: 2, Carton_List: ['TRU_G01_50W_AT|||||||6|0|TEST000001||001||||||||01', 'TRU_G01_50W_AT|||||||6|0|TEST000002||001||||||||02']}]},
};
function rawTpl() { $('#rawbody').value = JSON.stringify(RAW_TPL[$('#rawkind').value], null, 2); }
async function orderRaw(e) {
  e.preventDefault();
  let body; try { body = JSON.parse($('#rawbody').value); } catch (err) { return toast('JSON sai: ' + err.message, true); }
  done(await api(C('/api/order/raw'), {kind: $('#rawkind').value, body}));
}

let WCFG = null;
async function saveWcsCfg() {
  const c = readFields($('#cfg'), JSON.parse(JSON.stringify(WCFG.config)));
  c.identify_lines = readTable('il'); c.export_lines = readTable('el');
  const r = done(await api(C('/api/config'), c, 'PUT'));
  if (r.ok) show();
}

// ---------------------------------------------------------------- đăng ký trang
page('c_dash', 'overview', 'Điều khiển (WCS)', 'WCS', async () => {
  const d = await api(C('/api/dashboard')), k = d.tiles;
  return `<div class="tiles">
    ${tile('Task robot đang chạy', fmt(k.move_running), `${fmt(k.move_waiting)} đang chờ`)}
    ${tile('Task robot xong hôm nay', fmt(k.move_done_today), `${fmt(k.move_fail_today)} lỗi / hủy / bỏ qua`)}
    ${tile('Pallet định danh hôm nay', fmt(k.pallet_identified_today), 'đã báo WMS')}
    ${tile('DO đang xuất', fmt(k.do_running), `${fmt(k.do_done_today)} xong hôm nay`)}
    ${tile('Bản tin chờ gửi', fmt(k.outbox_pending), k.outbox_dead ? `<span class="st crit"><i>▲</i>${k.outbox_dead} bị từ chối</span>` : 'không có bản tin lỗi')}
    ${tile('Cảnh báo 24h', fmt(k.alerts_24h), '')}
    ${tile('RCS', status(d.rcs_ok), 'kết quả lần gọi gần nhất')}
    ${tile('WMS', status(d.wms_ok, 'Kết nối', 'Đang gửi lại'), 'bản tin gần nhất')}
  </div>
  <div class="grid2">
    <div class="card"><h3>Task robot hoàn thành theo giờ – hôm nay</h3>${hourBars(d.hourly_moves, 'task hoàn thành')}</div>
    <div class="card"><h3>Pallet định danh theo giờ – hôm nay</h3>${hourBars(d.hourly_pallets, 'pallet')}</div>
  </div>
  <div class="grid2">
    <div class="card"><h3>Ô kệ theo kho (WCS)</h3>${occupancy(d.occupancy)}</div>
    <div class="card"><h3>Trạm / Line</h3>${table(d.lines, [['Line', 'line_id'], ['Loại', 'kind'], ['Trạng thái', r => r.enabled ? status(true, 'Bật') : status(false, '', 'Tắt')],
      ['Đang làm', 'active', 'num'], ['Gọi về lần cuối', 'last_seen']])}</div>
  </div>
  <div class="grid2">
    <div class="card"><h3>Task robot lỗi gần đây</h3>${table(d.recent_fail, [['Giờ', 'final_at'], ['Task', 'task_id'], ['Pallet', 'pallet_id'], ['TT', r => badge(r.status)], ['Lỗi', 'error']])}</div>
    <div class="card"><h3>Cảnh báo gần đây</h3>${table(d.alerts, [['Giờ', 't'], ['Mức', r => badge(r.level)], ['Nội dung', 'text']])}</div>
  </div>`;
});

page('c_order', 'order', 'Lệnh tay / bản tin test', 'WCS', async () => `
  <div class="card"><h3>Move tay (lệnh của WCS B, không báo WMS)</h3>
   <div class="hint">Mỗi bước dạng <code>TỪ&gt;ĐẾN</code>, nhiều bước cách nhau dấu phẩy hoặc xuống dòng – chạy tuần tự như một nhóm n/m.</div>
   <form class="f" onsubmit="orderMove(event)">
    <label class="fl">Pallet<input name="pallet_id" required placeholder="LPNB00001"></label>
    <label class="fl" style="flex:1">Các bước<input name="steps" required placeholder="BDD09>C24-1-1, C24-1-1>C24-2-1" style="min-width:320px"></label>
    <label class="fl">Robot<select name="type_robot"><option value="1">1 – nâng hạ</option><option value="2">2 – đội gầm</option></select></label>
    <label class="fl">Type_Move<input name="type_move" type="number" value="11"></label>
    <button class="p">Đặt lệnh</button></form></div>
  <div class="card"><h3>Gửi bản tin WMS (test)</h3>
   <div class="hint">Chạy đúng như WMS gọi vào WCS B, có báo kết quả về WMS. Bấm "Mẫu" để có bản tin ví dụ.</div>
   <form class="f" onsubmit="orderRaw(event)" style="flex-direction:column;align-items:stretch">
    <div class="row"><label class="fl">Loại<select id="rawkind" onchange="rawTpl()"><option value="move">Move pallet</option><option value="identify">Định danh PO</option>
      <option value="import">Định danh ASN</option><option value="cancel">Hủy định danh</option><option value="export">Xuất DO</option></select></label>
      <button type="button" class="a" onclick="rawTpl()">Mẫu</button></div>
    <textarea id="rawbody" spellcheck="false"></textarea>
    <div><button class="p">Gửi</button></div></form></div>`, rawTpl);

page('c_moves', 'control', 'Task robot', 'WCS', async () => table(await api(C('/api/moves')), [['Tạo lúc', 'created_at'], ['Task_ID', 'task_id'], ['Pallet', 'pallet_id'], ['Từ', 'loc_old'], ['Đến', 'loc_new'],
  ['Robot', 'type_robot'], ['Move', 'type_move'], ['Trạng thái', r => badge(r.status)], ['orderId RCS', 'order_id'],
  ['RCS', r => esc(r.rcs_status ?? '') + ' ' + esc(r.device || '')], ['Báo WMS', r => badge(r.reported)], ['Lần', 'attempt', 'num'], ['Lỗi', 'error'],
  ['Thao tác', r => ['CREATED', 'DISPATCHED', 'RUNNING'].includes(r.status)
    ? `<button class=a onclick="act('cancel','${esc(r.task_id)}')">Hủy</button> <button class=a onclick="act('complete','${esc(r.task_id)}')">Xong tay</button>`
    : ['FAILED', 'CANCELLED', 'SKIPPED'].includes(r.status) ? `<button class=a onclick="act('retry','${esc(r.task_id)}')">Chạy lại</button>` : '']]));

page('c_identify', 'control', 'Định danh tại bàn', 'WCS', async () => {
  const rows = await api(C('/api/identify'));
  return rows.map(o => `<div class="card"><h3>${esc(o.kind)} ${esc(o.po_id)} · ${esc(o.po_name)} · line ${esc(o.line_id)} · ${badge(o.status)} · ${o.pallets_done}/${o.pallets_total} pallet</h3>` +
    table(o.pallets, [['Task_ID', 'task_id'], ['Pallet', 'pallet_id'], ['Thùng KH', 'ctn_plan', 'num'], ['Thùng đạt', 'ctn', 'num'],
      ['Trạng thái', r => badge(r.status)], ['Vị trí WMS', 'wms_location'], ['Lỗi', 'error']]) + '</div>').join('') || '<div class=empty>Chưa có lệnh</div>';
});

page('c_export', 'control', 'Xuất tại băng tải', 'WCS', async () => table(await api(C('/api/export')), [['Tạo lúc', 'created_at'], ['DO', 'do_id'], ['Băng tải', 'line_id'], ['Trạng thái', r => badge(r.status)],
  ['Pallet', r => (r.done_pallets || []).length + '/' + (r.pallets || []).length, 'num'], ['Danh sách', r => esc((r.pallets || []).join(', '))],
  ['Lỗi', 'error'], ['', r => ['QUEUED', 'RUNNING'].includes(r.status) ? `<button class=a onclick="doClose(${r.do_id})">Đóng tay</button>` : '']]));

page('c_lines', 'control', 'Trạm / Line', 'WCS', async () => '<div class="card"><div class="hint">Cutover: TẮT line trên WCS B = không nhận lệnh mới cho line đó. Thêm / sửa line ở Hệ thống → Cấu hình WCS.</div>' +
  table(await api(C('/api/lines')), [['Line', 'line_id'], ['Loại', 'kind'], ['Tên', 'name'], ['URL trạm', 'url'], ['Điểm sàn', 'location'],
    ['Trạng thái', r => r.enabled ? status(true, 'Bật') : status(false, '', 'Tắt')], ['DO đang chạy', 'busy_do'], ['Gọi về lần cuối', 'last_seen'],
    ['', r => `<button class=a onclick="lineEnable('${esc(r.line_id)}', ${!r.enabled})">${r.enabled ? 'Tắt' : 'Bật'}</button>`]]) + '</div>');

page('c_locs', 'control', 'Ô kệ (WCS)', 'WCS', async () => {
  const rows = (await api(C('/api/locations'))).filter(l => l.kind === 'RACK');
  return '<div class="hint">Trạng thái ô phía WCS (giữ chỗ khi gọi robot). Bấm ô để xem và chọn thao tác.</div><div class=grid>' + rows.map(l =>
    `<div class="cell ${l.state}" onclick="wcsCell('${esc(l.code)}')" data-tip="${esc(l.zone)} · ${esc(l.state)} ${esc(l.task || '')}"><b>${esc(l.code)}</b><br>${badge(l.state)}<br>${esc(l.pallet_id || '')}</div>`).join('') + '</div>';
});

page('c_alerts', 'control', 'Cảnh báo', 'WCS', async () => table(await api(C('/api/alerts')), [['Giờ', 't'], ['Mức', r => badge(r.level)], ['Nội dung', 'text'], ['Tham chiếu', 'ref']]));

page('c_outbox', 'system', 'Outbox WCS', 'WCS', async () => table(await api(C('/api/outbox')), [['Tạo', 'created_at'], ['Key', 'key'], ['Loại', 'kind'], ['Bên nhận', 'peer'], ['Trạng thái', r => badge(r.status)],
  ['Lần', 'tries', 'num'], ['Lỗi cuối', 'last_error'], ['', r => r.status !== 'DONE' ? `<button class=a onclick="obRetry('${esc(r.key)}')">Gửi lại</button>` : '']]));
page('c_msg', 'system', 'Bản tin WCS', 'WCS', async () => msgTable(await api(C('/api/messages'))));
page('c_cfg', 'system', 'Cấu hình WCS', 'WCS', async () => {
  WCFG = await api(C('/api/config'));
  const c = WCFG.config;
  return `<form id="cfg" onsubmit="return false"><div class="grid2">
   <div class="card"><h3>WMS</h3><div class="row">
    ${fld('URL WMS', 'wms.url', c.wms.url, 'str', 'style="min-width:260px"')}${fld('Tài khoản', 'wms.user', c.wms.user)}
    ${fld('Mật khẩu', 'wms.password', c.wms.password, 'str', 'type="password"')}${fld('App_Name', 'wms.app_name', c.wms.app_name)}</div></div>
   <div class="card"><h3>RCS (HuaRay ICS)</h3><div class="row">
    ${fld('URL RCS', 'rcs.url', c.rcs.url, 'str', 'style="min-width:220px"')}${fld('areaId', 'rcs.area_id', c.rcs.area_id, 'num')}
    ${fld('fromSystem', 'rcs.from_system', c.rcs.from_system)}${fld('Tiền tố orderId', 'rcs.order_prefix', c.rcs.order_prefix)}
    ${fld('Hỏi trạng thái (s)', 'rcs.poll_s', c.rcs.poll_s, 'num')}${fld('Timeout (s)', 'rcs.timeout_s', c.rcs.timeout_s, 'num')}
    ${fld('Cảnh báo chạy lâu (phút)', 'rcs.running_alert_min', c.rcs.running_alert_min, 'num')}
    ${fld('Template robot 1 (nâng hạ)', 'rcs.templates.1', c.rcs.templates['1'])}${fld('Template robot 2 (đội gầm)', 'rcs.templates.2', c.rcs.templates['2'])}
    ${fld('Type_Move ưu tiên cao', 'rcs.high_priority_moves', c.rcs.high_priority_moves, 'list')}
    <label class="fl">taskPath gửi RCS<select data-k="rcs.path_by" data-t="str"><option value="code" ${c.rcs.path_by !== 'node' ? 'selected' : ''}>Mã vị trí (tên điểm)</option><option value="node" ${c.rcs.path_by === 'node' ? 'selected' : ''}>Node (file highbay)</option></select></label>
    ${fld('Priority cao', 'rcs.priority_high', c.rcs.priority_high, 'num')}${fld('Priority thường', 'rcs.priority_normal', c.rcs.priority_normal, 'num')}</div></div>
  </div>
  <div class="card"><h3>Bàn định danh (IDS)</h3><div class="hint">URL dạng http://&lt;IP trạm&gt;:8500/ids · Điểm sàn = mã vị trí nơi robot lấy pallet</div>
   ${editTable('il', c.identify_lines, [['line_id', 'Line_ID', 'str'], ['name', 'Tên', 'str'], ['url', 'URL trạm', 'wide'], ['location', 'Điểm sàn', 'str'], ['enabled', 'Bật', 'bool']])}</div>
  <div class="card"><h3>Băng tải xuất (SCS)</h3><div class="hint">URL dạng http://&lt;IP trạm&gt;:8700/scs</div>
   ${editTable('el', c.export_lines, [['line_id', 'Conveyor_Belt', 'str'], ['name', 'Tên', 'str'], ['url', 'URL trạm', 'wide'], ['location', 'Điểm sàn', 'str'], ['enabled', 'Bật', 'bool']])}</div>
  <div class="card"><h3>Khác</h3><div class="row">
    ${fld('Kiểm tra vị trí', 'locations.check', c.locations.check, 'bool')}
    ${fld('Gửi lại sau (s)', 'outbox.retry_s', c.outbox.retry_s, 'num')}${fld('Timeout gửi (s)', 'outbox.timeout_s', c.outbox.timeout_s, 'num')}
    ${fld('Chu kỳ xử lý (s)', 'worker_s', c.worker_s, 'num')}${fld('Giữ bản tin (ngày)', 'messages_keep_days', c.messages_keep_days, 'num')}</div>
    <div class="hint" style="margin-top:10px">Cổng ${WCFG.server.port}, MongoDB ${esc(WCFG.mongo.uri)} / ${esc(WCFG.mongo.db)}: sửa trong ${esc(WCFG.file)} rồi chạy lại run_wcs.bat.</div></div>
  <div class="savebar"><button type="button" class="p" onclick="saveWcsCfg()">Lưu và áp dụng</button><span class="hint">Ghi ${esc(WCFG.file)} (giữ bản .bak), áp dụng ngay không cần khởi động lại.</span></div>
  </form>`;
});
