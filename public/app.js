'use strict';
const $ = (id) => document.getElementById(id);
let state = { profiles: {}, events: [] }, original = null, deleting = null, toastTimer;
const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notify(message, error = false) {
  clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = error ? 'error' : ''; $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 9000 : 4500);
}
async function api(action, body) {
  const response = await fetch('/api/' + action, body ? {method:'POST', headers:{'Content-Type':'application/json','X-Local-Token':state.token}, body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败');
  return data;
}
function formatTime(value) { if (!value) return '未知'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '未知' : date.toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
function render() {
  const query = $('search').value.trim().toLowerCase();
  const profiles = Object.entries(state.profiles).filter(([name, p]) => [name, p.user_data_dir, p.proxy].join(' ').toLowerCase().includes(query));
  $('count').textContent = Object.keys(state.profiles).length;
  $('profiles').innerHTML = profiles.map(([name, p]) => {
    const dates = state.profile_meta?.[name] || {};
    return `<tr><td><span class="profile-name"><span class="avatar" aria-hidden="true">${esc(name.slice(0,1).toUpperCase())}</span><span><strong>${esc(name)}</strong><small>${esc(p.profile_directory || 'Default')} · ${(p.urls || []).length} 个网页</small></span></span></td><td class="path-cell" title="${esc(p.user_data_dir)}">${esc(p.user_data_dir)}</td><td><span class="tag">${p.proxy ? '代理网络' : '系统网络'}</span><small class="network-detail">${esc(p.proxy || '默认网络')}</small></td><td class="date-cell">${formatTime(dates.created_at)}</td><td class="date-cell">${formatTime(dates.updated_at)}</td><td><div class="table-actions"><button class="primary" data-action="launch" data-name="${esc(name)}" ${state.chrome_binary ? '' : 'disabled'}>启动</button><button data-action="preview" data-name="${esc(name)}">预览</button><button data-action="edit" data-name="${esc(name)}">编辑</button><button data-action="duplicate" data-name="${esc(name)}">复制</button><button class="remove" data-action="delete" data-name="${esc(name)}" aria-label="移除 ${esc(name)}" title="移除配置">×</button></div></td></tr>`;
  }).join('');
  $('profiles').closest('.table-scroll').hidden = profiles.length === 0;
  $('empty').hidden = profiles.length > 0;
  $('events').innerHTML = state.events.length ? state.events.slice(0, 6).map((event) => `<div class="event"><span class="event-icon">↗</span><div><strong>${esc(event.name)}</strong><small>已发送启动请求</small></div><time datetime="${esc(event.time)}" title="${esc(new Date(event.time).toLocaleString())}">${esc(new Date(event.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}))}</time></div>`).join('') : '<p class="muted">还没有启动记录</p>';
  $('health-dot').className = state.chrome_binary ? 'dot' : 'dot bad';
  $('health-title').textContent = state.chrome_binary ? 'Chrome 已就绪' : '未找到 Chrome';
  $('binary').textContent = state.chrome_binary || state.chrome_error;
}
async function refresh() { state = await api('state'); render(); }
function edit(name = null) {
  original = name;
  $('profile-form').reset(); $('form-error').textContent = '';
  $('editor-title').textContent = name ? '编辑配置' : '新建配置';
  const p = name ? state.profiles[name] : {profile_directory:'Default',args:['--no-first-run'],urls:['https://www.google.com']};
  const f = $('profile-form').elements;
  f.namedItem('name').value = name || '';
  for (const key of ['user_data_dir','profile_directory','proxy','proxy_bypass_list']) f.namedItem(key).value = p[key] || '';
  for (const key of ['urls','args']) f.namedItem(key).value = (p[key] || []).join('\n');
  $('editor').showModal();
}
$('new').onclick = () => edit(); $('empty-new').onclick = () => edit();
$('search').oninput = render;
$('close-editor').onclick = $('cancel-editor').onclick = () => $('editor').close();
$('close-preview').onclick = () => $('preview').close();
$('cancel-delete').onclick = () => $('delete-dialog').close();
$('profile-form').onsubmit = async (event) => {
  event.preventDefault(); const f = $('profile-form').elements;
  const profile = {};
  for (const key of ['user_data_dir','profile_directory','proxy','proxy_bypass_list']) profile[key] = f.namedItem(key).value.trim();
  for (const key of ['urls','args']) profile[key] = f.namedItem(key).value.split('\n').map((v) => v.trim()).filter(Boolean);
  $('save').disabled = true; $('form-error').textContent = '';
  try {
    const result = await api('save', {name:f.namedItem('name').value.trim(), profile, original});
    $('editor').close(); notify(result.message); await refresh();
  } catch (error) { $('form-error').textContent = error.message; }
  finally { $('save').disabled = false; }
};
$('profiles').onclick = async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const {action, name} = button.dataset;
  if (action === 'edit') return edit(name);
  if (action === 'delete') {
    deleting = name; $('delete-description').textContent = `即将移除「${name}」。`; $('delete-dialog').showModal(); return;
  }
  button.disabled = true;
  try {
    const result = await api(action, {name});
    if (action === 'preview') { $('command').textContent = JSON.stringify(result.command, null, 2); $('preview').showModal(); }
    else { notify(result.message); await refresh(); }
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
};
$('confirm-delete').onclick = async () => {
  $('confirm-delete').disabled = true;
  try { const result = await api('delete', {name:deleting}); $('delete-dialog').close(); notify(result.message); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { $('confirm-delete').disabled = false; }
};
$('copy').onclick = async () => {
  try { await navigator.clipboard.writeText($('command').textContent); notify('已复制启动参数'); }
  catch { notify('复制失败，请手动选择参数文本复制', true); }
};
refresh().catch((error) => { $('health-title').textContent = '本地服务连接失败'; $('binary').textContent = error.message; $('health-dot').classList.add('bad'); notify(error.message, true); });
