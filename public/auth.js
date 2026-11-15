'use strict';
const $ = id => document.getElementById(id);
let state = null;
let deleting = null;
let editing = null;
let timer;
let usageTimer;
let countdownTimer;
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const usageLevel = used => used >= 80 ? 'usage-high' : used >= 50 ? 'usage-medium' : 'usage-low';
function toast(message, error=false) { clearTimeout(timer); $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; timer=setTimeout(()=>$('toast').hidden=true,5000); }
async function api(action, name, newName) {
  const response=await fetch('/api/auth/'+action, action==='state'?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.token},body:JSON.stringify(name===undefined?{}:{name,...(newName===undefined?{}:{new_name:newName})})});
  const data=await response.json(); if(!response.ok) throw new Error(data.error||'操作失败'); return data;
}
async function loadUsage(force=false) {
  const button=$('usage-refresh');
  if (state && !state.hasActive) {
    button.disabled=true;
    $('usage-updated').textContent='等待登录';
    $('usage-content').innerHTML='<p class="usage-empty">请登录 Codex 后刷新此页。</p>';
    return;
  }
  button.disabled=true;
  try {
    const response=await fetch('/api/auth/usage'+(force?'?refresh=1':''),{cache:'no-store'});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||'用量读取失败');
    renderUsage(data);
  } catch(error) {
    $('usage-content').innerHTML=`<p class="usage-empty error-text">${escapeHtml(error.message)}</p>`;
    $('usage-updated').textContent='用量暂不可用';
  } finally { button.disabled=false; }
}
function renderUsage(data) {
  const limits=data.rateLimits||{}; const windows=[['短期窗口',limits.primary],['长期窗口',limits.secondary]];
  const rows=windows.filter(([,w])=>w&&Number.isFinite(w.usedPercent)).map(([label,w])=>{
    const used=Math.min(100,Math.max(0,w.usedPercent));
    const duration=w.windowDurationMins;
    const title=duration>=10080?'Weekly limit':duration===300?'5-hour limit':label==='短期窗口'?'Short-term limit':'Long-term limit';
    const reset=Number.isFinite(w.resetsAt)?w.resetsAt*1000:null;
    const left=100-used, level=usageLevel(used);
    return `<article class="usage-meter"><div class="usage-label"><strong>${title}</strong></div><p class="usage-countdown" ${reset?`data-reset="${reset}"`:''}>${reset?'':'Reset time unavailable'}</p><strong class="usage-left ${level}">${left}% left</strong><progress class="meter-track ${level}" max="100" value="${left}" aria-label="${left}% left"></progress></article>`;
  }).join('');
  const credits=data.rateLimitResetCredits?.availableCount;
  const creditText=Number.isInteger(credits)?`可用重置次数：${credits}`:'可用重置次数暂不可用';
  const resetCredits=data.rateLimitResetCredits?.credits;
  const resetRows=Array.isArray(resetCredits)?resetCredits.map(credit=>{
    const received=Number.isFinite(credit.grantedAt)?formatTime(new Date(credit.grantedAt*1000)):'未知';
    const expiry=Number.isFinite(credit.expiresAt)?` · 到期 ${formatTime(new Date(credit.expiresAt*1000))}`:'';
    return `<li>收到可用重置 · ${received}${expiry}</li>`;
  }).join(''):'';
  const resetPanel=`<section class="reset-summary"><h3>Usage limit resets</h3><p>可使用重置恢复 5 小时限额、周限额或两者，具体以 Codex 提供的选项为准。</p><strong>${creditText}</strong>${resetRows?`<ul>${resetRows}</ul>`:''}<p class="muted">当前接口提供可用次数和可用重置详情，暂未提供过去 30 天的完整领取与使用记录。</p></section>`;
  const plan=limits.planType||data.planType;
  $('usage-content').innerHTML=`${rows||'<p class="usage-empty">账号暂未提供可显示的限额信息。</p>'}<div class="usage-meta">${plan?`方案：${escapeHtml(plan)}`:''}</div>${resetPanel}`;
  $('usage-updated').textContent=`更新于 ${formatTime(data.fetchedAt)}`;
  updateCountdowns();
  clearInterval(countdownTimer);
  countdownTimer=setInterval(updateCountdowns,1000);
}
function countdownText(resetAt) {
  const seconds=Math.max(0,Math.ceil((resetAt-Date.now())/1000));
  if(seconds<=0)return 'Resetting now';
  const minutes=Math.ceil(seconds/60);
  if(minutes<60)return `${minutes}m`;
  const hours=Math.floor(minutes/60), remainingMinutes=minutes%60;
  if(hours<24)return remainingMinutes?`${hours}h ${remainingMinutes}m`:`${hours}h`;
  const days=Math.floor(hours/24), remainingHours=hours%24;
  return remainingHours?`${days}d ${remainingHours}h`:`${days}d`;
}
function updateCountdowns() {
  document.querySelectorAll('.usage-countdown[data-reset]').forEach(element=>{
    element.textContent=`Resets in ${countdownText(Number(element.dataset.reset))}`;
  });
}
function usageCell(window) {
  if (!window || !Number.isFinite(window.usedPercent)) return '<span class="usage-pending">—</span>';
  const used=Math.min(100,Math.max(0,window.usedPercent));
  const left=100-used;
  const level=usageLevel(used);
  const reset=Number.isFinite(window.resetsAt)?window.resetsAt*1000:null;
  return `<div class="table-usage"><div><strong class="${level}">${left}% left</strong><small class="usage-countdown" ${reset?`data-reset="${reset}"`:''}>${reset?`Resets in ${countdownText(reset)}`:'Reset time unavailable'}</small></div><progress class="table-meter ${level}" max="100" value="${left}" aria-label="${left}% left"></progress></div>`;
}
function applyProfileUsage(name, data, error) {
  const row=[...$('profiles').querySelectorAll('tr')].find(item=>item.dataset.name===name);
  if(!row)return;
  if(error){
    const stale=/token_revoked|invalidated oauth token|invalid_jwt|signing in again/i.test(error);
    const message=stale?'认证失效，请重新登录':'查询失败';
    row.querySelector('.profile-usage-state').textContent=message;
    row.querySelector('.profile-usage-state').title=message;
    row.querySelector('[data-period="short"]').innerHTML=`<span class="usage-pending">${message}</span>`;
    row.querySelector('[data-period="week"]').innerHTML=`<span class="usage-pending">${message}</span>`;
    return;
  }
  const limits=data.rateLimits||{};
  row.querySelector('[data-period="short"]').innerHTML=usageCell(limits.primary);
  row.querySelector('[data-period="week"]').innerHTML=usageCell(limits.secondary);
  row.querySelector('.profile-plan').textContent=limits.planType||data.planType||'—';
  const resetCount=data.rateLimitResetCredits?.availableCount;
  if(Number.isInteger(resetCount)){
    const detail=document.createElement('small');
    detail.className='table-profile-status';
    detail.textContent=`可用重置 ${resetCount} 次`;
    row.querySelector('.profile-plan').append(detail);
  }
  row.querySelector('.profile-usage-state').textContent=`更新于 ${formatTime(data.fetchedAt)}`;
}
async function loadProfileUsages(force=false) {
  await Promise.all((state?.profiles||[]).map(async profile=>{
    try {
      const params=new URLSearchParams({profile:profile.name}); if(force)params.set('refresh','1');
      const response=await fetch('/api/auth/usage?'+params.toString(),{cache:'no-store'});
      const data=await response.json(); if(!response.ok)throw new Error(data.error||'读取失败');
      applyProfileUsage(profile.name,data);
    } catch(error) { applyProfileUsage(profile.name,null,error.message); }
  }));
}
function formatTime(value) {
  if (!value) return '未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '未知' : date.toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function render() {
  $('count').textContent=state.profiles.length;
  $('status-dot').className='dot'+(state.hasActive?'':' warn');
  $('status-title').textContent=state.hasActive?'当前认证已就绪':'当前没有 auth.json';
  $('status-detail').textContent=state.hasActive?'可以备份当前认证，或切换至已保存档案。':'请先登录 Codex，再备份认证。';
  $('backup').disabled=!state.hasActive; $('restore').disabled=!state.canRestore;
  $('remove-active').disabled=!state.hasActive;
  $('empty').hidden=state.profiles.length!==0;
  $('profiles').innerHTML=state.profiles.map(p=>`<tr data-name="${escapeHtml(p.name)}"><td><strong class="table-profile-name" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</strong><small class="table-profile-status">${p.active?'当前认证':'已保存档案'} · 更新 ${formatTime(p.updated_at)}</small><small class="profile-usage-state">正在读取…</small></td><td data-period="short"><span class="usage-pending">读取中…</span></td><td data-period="week"><span class="usage-pending">读取中…</span></td><td class="profile-plan">—</td><td class="table-actions">${p.active?'<span class="active">✓ 当前</span>':`<button class="switch" data-name="${escapeHtml(p.name)}">切换</button>`}<button class="edit" data-edit="${escapeHtml(p.name)}">编辑</button><button class="delete" data-delete="${escapeHtml(p.name)}" aria-label="删除 ${escapeHtml(p.name)}" title="删除档案">×</button></td></tr>`).join('');
  $('suggestions').replaceChildren(...(state.suggestions||[]).map(name=>{const b=document.createElement('button');b.type='button';b.textContent=name;b.onclick=()=>$('name').value=name;return b;}));
}
async function refresh() { state=await api('state');render();await loadProfileUsages(); }
$('refresh').onclick=()=>refresh().then(()=>loadUsage(true)).catch(e=>toast(e.message,true));
$('usage-refresh').onclick=()=>loadUsage(true);
$('remove-active').onclick=async()=>{
  if (!window.confirm('移除 ~/.codex/auth.json？如果要保留这个账号，请先备份。移除会覆盖「上一次认证」，已命名的档案会保留。')) return;
  const button=$('remove-active');
  button.disabled=true;
  try {
    const result=await api('remove-active');
    state=await api('state');
    render();
    await loadUsage();
    toast(result.message);
    await loadProfileUsages();
  } catch (error) {
    toast(error.message,true);
  } finally {
    button.disabled=!state.hasActive;
  }
};
$('backup').onclick=()=>{ $('form-error').textContent='';$('name').value=state.suggestions?.[0]||'';$('backup-dialog').showModal(); };
$('close').onclick=$('cancel').onclick=()=>$('backup-dialog').close();
$('backup-form').onsubmit=async e=>{e.preventDefault();const button=$('backup-form').querySelector('[type=submit]');button.disabled=true;try{const r=await api('backup',$('name').value.trim());$('backup-dialog').close();toast(r.message);await refresh();}catch(error){$('form-error').textContent=error.message;}finally{button.disabled=false;}};
$('profiles').onclick=async e=>{const editButton=e.target.closest('button[data-edit]');if(editButton){editing=editButton.dataset.edit;$('edit-name').value=editing;$('edit-error').textContent='';$('edit-dialog').showModal();return;}const deleteButton=e.target.closest('button[data-delete]');if(deleteButton){deleting=deleteButton.dataset.delete;$('delete-description').textContent=`即将删除「${deleting}」。`; $('delete-dialog').showModal();return;}const button=e.target.closest('button[data-name]');if(!button)return;button.disabled=true;try{const r=await api('activate',button.dataset.name);toast(r.message);await refresh();await loadUsage(true);}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
$('close-edit').onclick=$('cancel-edit').onclick=()=>$('edit-dialog').close();
$('edit-form').onsubmit=async e=>{e.preventDefault();const button=$('edit-form').querySelector('[type=submit]');button.disabled=true;try{const r=await api('rename',editing,$('edit-name').value.trim());$('edit-dialog').close();toast(r.message);await refresh();}catch(error){$('edit-error').textContent=error.message;}finally{button.disabled=false;}};
$('close-delete').onclick=$('cancel-delete').onclick=()=>$('delete-dialog').close();
$('confirm-delete').onclick=async()=>{const button=$('confirm-delete');button.disabled=true;try{const r=await api('delete',deleting);$('delete-dialog').close();toast(r.message);await refresh();}catch(error){toast(error.message,true);}finally{button.disabled=false;}};
$('restore').onclick=async()=>{$('restore').disabled=true;try{const r=await api('restore');toast(r.message);await refresh();await loadUsage(true);}catch(error){toast(error.message,true);$('restore').disabled=false;}};
refresh().then(()=>loadUsage()).catch(e=>{$('status-title').textContent='本地服务连接失败';$('status-detail').textContent=e.message;$('status-dot').className='dot warn';toast(e.message,true);});
let automaticRefreshRunning = false;
usageTimer = setInterval(async () => {
  if (automaticRefreshRunning || !state) return;
  automaticRefreshRunning = true;
  try {
    await Promise.all([loadUsage(), loadProfileUsages()]);
  } finally {
    automaticRefreshRunning = false;
  }
}, 60000);
