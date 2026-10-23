
const $=id=>document.getElementById(id);
let currentPage='live',daySelected=localDate(),settings={},daysCache=[],statusCache=null,toastTimer=null;
let zonesEditing=false,zoneDraft=null,zonePoints=[],lastZoneClick=0;
function localDate(d=new Date()){let y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),v=String(d.getDate()).padStart(2,'0');return `${y}-${m}-${v}`}
const localTime=(s,seconds=false)=>new Date(s*1000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit'}:{})});
const fmtSize=n=>n>=1024**3?(n/1024**3).toFixed(2)+' GB':n>=1024**2?(n/1024**2).toFixed(1)+' MB':Math.ceil(n/1024)+' KB';
const fmtDuration=n=>n>=60?`${Math.floor(n/60)} 分 ${Math.round(n%60)} 秒`:`${Math.round(n)} 秒`;
const levelInfo={idle:['静止','#71869b','静止'],low:['轻微活动','#7fe6ce','轻微'],medium:['一般活动','#ffd17d','一般'],high:['显著活动','#ff7585','显著']};
function safe(s){return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;')}
function notify(s,err=false){$('toast').textContent=s;$('toast').style.borderColor=err?'#9b4d64':'#57848c';$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),4200)}
async function getJSON(url){let r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error(`${r.status}: ${await r.text()}`);return r.json()}
async function send(url,method='POST',body){let r=await fetch(url,{method,headers:{'X-Local-UI':'1','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok){let data=await r.text();throw Error(data.slice(0,200))}return r.json()}
function renderLiveBars(data){const now=Math.floor(new Date(statusCache?.server_time||Date.now()).getTime()/1000),values=new Map((data||[]).map(v=>[v[0],v[1]]));let html='';for(let i=179;i>=0;i--){const sec=now-i,lev=values.get(sec)||'idle';html+=`<span class="${lev}" title="${localTime(sec,true)} · ${levelInfo[lev]?.[0]||'静止'}"></span>`;}$('livebars').innerHTML=html}
function switchPage(page){currentPage=page;['live','archive','settings'].forEach(p=>{$('page-'+p).classList.toggle('hidden',p!==page);$('nav-'+p).classList.toggle('active',p===page)});let titles={live:['实时监控中心','查看实时画面，按运动事件节省录像空间。'],archive:['录像资料库','按日期筛选、预览、下载与保护事件录像。'],settings:['录制与存储设置','自定义算法灵敏度、自动清理与磁盘配额。']};$('page-title').textContent=titles[page][0];$('page-subtitle').textContent=titles[page][1];if(page==='archive')loadArchive();if(page==='settings'&&statusCache)fillSettings(settings)}
function refreshClock(){let d=new Date();$('clock').textContent=d.toLocaleTimeString('zh-CN',{hour12:false});$('date-label').textContent=d.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'})}setInterval(refreshClock,1000);refreshClock();
async function poll(){try{let s=await getJSON('/api/status');statusCache=s;settings=s.settings;let c=s.camera,online=c.online&&(new Date(s.server_time).getTime()/1000-c.last_frame)<7;$('pipeline-metrics').textContent=`采集 ${c.capture_fps||0} fps / 分析 ${c.actual_analysis_fps||0} fps
处理 P95 ${c.processing_p95_ms||0} ms
相机获取 ${c.frames_captured||0} 帧 · 覆盖丢弃 ${c.frames_overwritten||0}
分析跳过 ${c.frames_skipped||0} 帧
编码等待 ${c.encoder_queue_depth||0} · 丢弃 ${c.encoder_frames_dropped||0}
CFR 补帧 ${c.encoder_frames_duplicated||0} · 编码失败 ${c.encoder_failures||0}
后台清理 ${c.last_cleanup_seconds||0} s · 设备 ${c.storage_online?'正常':'离线'}`;
renderLiveBars(c.activity_seconds);$('stat-camera').textContent=online?'已连接':'未连接';$('stat-camera').style.color=online?'var(--accent)':'var(--red)';$('stat-source').textContent=c.source;$('stat-motion').textContent=c.state==='calibrating'?'校准中':c.state==='motion'?'活动中':'静止待机';$('stat-motion').style.color=c.state==='motion'?'var(--yellow)':'var(--white)';$('stat-score').textContent=`变化强度 ${Number(c.score).toFixed(2)}%`;$('stat-events').textContent=s.events;$('stat-storage').textContent=fmtSize(s.storage.used);$('stat-disk').textContent=`可用 ${fmtSize(s.storage.free)} · 上限 ${fmtSize(s.storage.max_bytes)}`;
$('camera-side-dot').classList.toggle('on',online);$('side-state').textContent=online?c.recording_clip?'正在保存事件录像':s.settings.record_enabled?'智能录像已开启':'仅预览 · 尚未开启录制':'摄像头断开';$('live-dot').classList.toggle('on',online);$('live-label').textContent=c.recording_clip?'录制中':online?'画面在线':'设备离线';$('record-toggle').textContent=s.settings.record_enabled?'停止智能录像':'开启智能录像';$('record-toggle').className=s.settings.record_enabled?'btn danger':'btn primary';$('motion-chip').textContent=c.state==='motion'?'● 检测到变化':'○ 静止';$('motion-level').textContent=(levelInfo[c.level]||levelInfo.idle)[2].toUpperCase();$('motion-level').style.color=(levelInfo[c.level]||levelInfo.idle)[1];$('motion-fill').style.width=Math.min(100,c.score*8)+'%';$('motion-pct').textContent=Number(c.score).toFixed(2)+'%';$('motion-hint').textContent=c.state==='calibrating'?'正在初始化背景模型，请保持画面相对稳定。':c.state==='motion'?'发现有效前景变化。若已开启录制，将生成事件 MP4。':s.settings.record_enabled?'静止待机：只分析运动，不保存持续视频。':'当前仅实时预览；请点击开启智能录像。';
$('offline-overlay').classList.toggle('hidden',online);$('overlay-main').textContent='摄像头未就绪';$('overlay-help').textContent=c.error||'检查 /dev/video0 权限、设备是否占用，或使用演示模式。';if(currentPage==='settings')$('storage-location').textContent=`录像目录：${s.storage.root}。为节省 SSD，推荐配置到 HDD /mnt/sda4。`;
}catch(e){$('stat-camera').textContent='服务器断开';$('overlay-main').textContent='无法连接后端';$('overlay-help').textContent=String(e);$('offline-overlay').classList.remove('hidden')}}

const zoneColors={include:'#56aeef',exclude:'#f1c15b',privacy:'#111111'};
function currentZones(){return zoneDraft||{include:[],exclude:[],privacy:[]}}
function zoneBounds(){
  let cvs=$('zone-canvas'),w=cvs.clientWidth,h=cvs.clientHeight,img=$('stream');
  let iw=img.naturalWidth||640,ih=img.naturalHeight||360,s=Math.min(w/iw,h/ih);
  return {x:(w-iw*s)/2,y:(h-ih*s)/2,w:iw*s,h:ih*s};
}
function renderZones(){
  let cvs=$('zone-canvas'),ratio=window.devicePixelRatio||1;
  cvs.width=Math.max(1,Math.round(cvs.clientWidth*ratio));
  cvs.height=Math.max(1,Math.round(cvs.clientHeight*ratio));
  let ctx=cvs.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,cvs.clientWidth,cvs.clientHeight);
  if(!zonesEditing)return;
  let rect=zoneBounds();ctx.lineWidth=2;
  for(let type of ['include','exclude','privacy']){
    for(let polygon of currentZones()[type]){
      ctx.beginPath();polygon.forEach(([x,y],i)=>i?ctx.lineTo(rect.x+x*rect.w,rect.y+y*rect.h):ctx.moveTo(rect.x+x*rect.w,rect.y+y*rect.h));
      ctx.closePath();ctx.fillStyle=type==='privacy'?'rgba(4,7,9,.78)':type==='include'?'rgba(86,174,239,.19)':'rgba(241,193,91,.24)';
      ctx.fill();ctx.strokeStyle=zoneColors[type];ctx.stroke();
    }
  }
  if(zonePoints.length){
    ctx.beginPath();zonePoints.forEach(([x,y],i)=>i?ctx.lineTo(rect.x+x*rect.w,rect.y+y*rect.h):ctx.moveTo(rect.x+x*rect.w,rect.y+y*rect.h));
    ctx.strokeStyle=zoneColors[$('zone-type').value];ctx.stroke();
    for(let [x,y] of zonePoints){ctx.beginPath();ctx.arc(rect.x+x*rect.w,rect.y+y*rect.h,4,0,Math.PI*2);ctx.fillStyle='#ffffff';ctx.fill()}
  }
}
function toggleZones(){
  if(zonesEditing){cancelZones();return}
  zonesEditing=true;zoneDraft=JSON.parse(JSON.stringify(settings.zones||{include:[],exclude:[],privacy:[]}));zonePoints=[];
  $('zone-canvas').classList.add('editing');$('zone-tools').classList.remove('hidden');$('zone-hint').classList.remove('hidden');
  $('zones-toggle').textContent='退出区域编辑';renderZones();
}
function cancelZones(){zonesEditing=false;zoneDraft=null;zonePoints=[];$('zone-canvas').classList.remove('editing');$('zone-tools').classList.add('hidden');$('zone-hint').classList.add('hidden');$('zones-toggle').textContent='编辑监测区域';renderZones()}
function completeZone(){
  if(zonePoints.length<3){notify('请先绘制至少 3 个顶点',true);return}
  let type=$('zone-type').value;
  if(currentZones()[type].length>=8){notify('该类型最多 8 个区域',true);return}
  // Reject near-zero-area polygons before hitting server validation.
  let area=Math.abs(zonePoints.reduce((sum,[x,y],i)=>{let [nx,ny]=zonePoints[(i+1)%zonePoints.length];return sum+x*ny-nx*y},0)/2);
  if(area<0.00005){notify('多边形过小，请重新绘制',true);return}
  zoneDraft[type].push(zonePoints.map(p=>[...p]));zonePoints=[];renderZones();
}
$('zone-canvas').addEventListener('click',e=>{
  if(!zonesEditing)return;
  // Suppress the second click of a double-click; dblclick closes the polygon.
  if(e.detail>=2)return;
  let b=zoneBounds(),r=$('zone-canvas').getBoundingClientRect(),x=(e.clientX-r.left-b.x)/b.w,y=(e.clientY-r.top-b.y)/b.h;
  if(x<0||x>1||y<0||y>1)return;
  if(zonePoints.length<32)zonePoints.push([+x.toFixed(5),+y.toFixed(5)]);
  renderZones();
});
$('zone-canvas').addEventListener('dblclick',e=>{if(zonesEditing){e.preventDefault();completeZone()}});
$('zone-type').addEventListener('change',()=>{zonePoints=[];renderZones()});
function undoZone(){if(zonePoints.length)zonePoints.pop();else currentZones()[$('zone-type').value].pop();renderZones()}
function clearZoneType(){currentZones()[$('zone-type').value]=[];zonePoints=[];renderZones()}
async function saveZones(){
  if(zonePoints.length){notify('请先双击完成当前多边形',true);return}
  if(!confirm('保存监测/隐私区域？当前录像会切段，且新遮挡无法修改历史视频。'))return;
  try{settings=await send('/api/settings','POST',{zones:zoneDraft});cancelZones();await poll();notify('新区域已应用到后端检测和录像')}catch(e){notify('区域保存失败：'+e.message,true)}
}
window.addEventListener('resize',()=>{if(zonesEditing)renderZones()});
$('stream').addEventListener('load',()=>{if(zonesEditing)renderZones()});

async function toggleRecord(){try{const enabling=!settings.record_enabled;if(enabling && !confirm('确认开始本机摄像头事件录制？请确保被拍摄者知情同意。'))return;await send('/api/settings','POST',{record_enabled:enabling});await poll();notify(enabling?'已开启智能录像；静止时不写入视频':'已停止录像，当前事件将完成收尾')}catch(e){notify(e.message,true)}}
function grade(score){return score>=8?'high':score>=2?'medium':'low'}
function eventRow(ev,compact=false){let intensity=grade(ev.peak_score),t=localTime(ev.start,true),dt=new Date(ev.start*1000).toLocaleDateString('zh-CN');let buttons=`<button class="btn small" data-action="play" data-id="${ev.id}">播放</button><a class="btn small ghost" href="/api/events/${ev.id}/download">下载</a><button class="btn small ghost" data-action="protect" data-id="${ev.id}" data-state="${ev.protected?1:0}">${ev.protected?'★ 已保护':'☆ 保护'}</button><button class="btn small danger" data-action="delete" data-id="${ev.id}">删除</button>`;
if(compact)return `<div class="clip-card"><img loading="lazy" src="/api/events/${ev.id}/thumb" alt="事件截图" data-action="play" data-id="${ev.id}"><div class="body"><b>${t} <span class="level-pill ${intensity}">${levelInfo[intensity][0]}</span></b><small>${fmtDuration(ev.duration)} · ${fmtSize(ev.bytes)}</small><div class="actions">${buttons}</div></div></div>`;
return `<div class="event-row"><img class="thumb" src="/api/events/${ev.id}/thumb" alt="事件缩略图" data-action="play" data-id="${ev.id}"><div><strong>${dt}　${t} <span class="level-pill ${intensity}">${levelInfo[intensity][0]}</span></strong><p>${fmtDuration(ev.duration)} · ${fmtSize(ev.bytes)} · 峰值变化 ${Number(ev.peak_score).toFixed(2)}%</p></div><div class="actions">${buttons}</div></div>`}
function buildTimeline(events){let el=$('timelinebar');el.replaceChildren();events.forEach(ev=>{let start=new Date(ev.start*1000),sec=start.getHours()*3600+start.getMinutes()*60+start.getSeconds();let btn=document.createElement('button');btn.className='seg';btn.style.left=(sec/86400*100)+'%';btn.style.width=Math.max(ev.duration/86400*100,.14)+'%';btn.style.background=levelInfo[grade(ev.peak_score)][1];btn.title=`${localTime(ev.start,true)} · ${fmtDuration(ev.duration)} · 点击回放`;btn.addEventListener('click',()=>play(ev.id));el.appendChild(btn)});$('timeline-count').textContent=events.length+' 个事件'}
async function loadRecent(){try{let today=statusCache?.today||localDate(),events=await getJSON('/api/events?day='+today+'&limit=120');buildTimeline(events);$('recent-list').innerHTML=events.length?events.slice(0,5).map(e=>eventRow(e)).join(''):'<div class="empty">今日还没有录像。开启智能录像后，摄像头检测到明显变化会自动保存事件。</div>'}catch(e){console.error(e)}}
async function loadArchive(){try{daysCache=await getJSON('/api/days');$('days-list').innerHTML=daysCache.length?daysCache.map(d=>`<button class="daybtn ${daySelected===d.day?'active':''}" data-day="${d.day}"><span>▣ ${d.day}</span><span>${d.count} 段</span></button>`).join(''):'<div class="tiny muted">暂无录制日期</div>';await loadDay(daySelected)}catch(e){notify('无法加载归档: '+e.message,true)}}
async function selectDay(day){if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return;daySelected=day;$('archive-date').value=day;await loadArchive()}
async function loadDay(day){let events=await getJSON('/api/events?day='+encodeURIComponent(day));$('archive-date').value=day;$('archive-day-label').textContent=day+' · 录像片段';$('archive-day-summary').textContent=`共 ${events.length} 段 · 合计 ${fmtDuration(events.reduce((a,e)=>a+e.duration,0))} · 文件 ${fmtSize(events.reduce((a,e)=>a+e.bytes,0))}`;$('clips-grid').innerHTML=events.length?events.map(e=>eventRow(e,true)).join(''):'<div class="empty">这一天没有录像。</div>'}
function fillSettings(s){$('sensitivity').value=s.sensitivity;$('sensitivity-value').textContent=Number(s.sensitivity).toFixed(2)+'%';$('minframes').value=s.min_motion_frames;$('postroll').value=String(s.post_roll_seconds);$('retention').value=String(s.retention_days);$('maxstorage').value=s.max_storage_gb;$('minfree').value=s.min_free_gb;$('showboxes').checked=s.show_boxes}
$('sensitivity').addEventListener('input',e=>$('sensitivity-value').textContent=Number(e.target.value).toFixed(2)+'%');
async function saveSettings(){try{let changed={sensitivity:Number($('sensitivity').value),min_motion_frames:Number($('minframes').value),post_roll_seconds:Number($('postroll').value),retention_days:Number($('retention').value),max_storage_gb:Number($('maxstorage').value),min_free_gb:Number($('minfree').value),show_boxes:$('showboxes').checked};await send('/api/settings','POST',changed);notify('设置已保存。新的保留策略将在下次清理时生效。');await poll()}catch(e){notify('无法保存设置: '+e.message,true)}}
async function cleanupNow(){if(!confirm('只会删除超过保留期限或达到空间上限的未保护录像，继续？'))return;try{let s=await send('/api/cleanup');notify(`清理完成：删除 ${s.deleted} 个录像事件`);loadArchive();poll()}catch(e){notify(e.message,true)}}
function play(id){$('modal-title').textContent='本地录像回放';$('modal-info').textContent='这段录像由运动检测触发，默认不包含音频。';let v=$('video');v.src=`/api/events/${id}/video`;v.load();$('modal').classList.remove('hidden');v.play().catch(()=>{})}
function closeModal(){$('video').pause();$('video').removeAttribute('src');$('video').load();$('modal').classList.add('hidden')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal()});
document.addEventListener('click',async e=>{let b=e.target.closest('[data-action]');if(!b)return;let {action,id,state}=b.dataset;if(action==='play'){play(id);return}try{if(action==='protect'){await send(`/api/events/${id}/protect`,'POST',{protected:state!=='1'});notify(state==='1'?'已取消保护':'已保护，自动清理将保留此录像')}else if(action==='delete'){if(!confirm('确定永久删除这段录像？不可撤回。'))return;await send(`/api/events/${id}`,'DELETE');notify('录像已删除')}await loadRecent();if(currentPage==='archive')await loadArchive();poll()}catch(e){notify(e.message,true)}});

document.addEventListener('click', e => {
  const day=e.target.closest('[data-day]');
  if(day){selectDay(day.dataset.day);return}
  const btn=e.target.closest('[data-ui-action]');
  if(!btn)return;
  switch(btn.dataset.uiAction){
    case 'live': switchPage('live'); break;
    case 'archive': switchPage('archive'); break;
    case 'settings': switchPage('settings'); break;
    case 'zones-toggle': toggleZones(); break;
    case 'toggle-record': toggleRecord(); break;
    case 'undo-zone': undoZone(); break;
    case 'clear-zone': clearZoneType(); break;
    case 'save-zones': saveZones(); break;
    case 'cancel-zones': cancelZones(); break;
    case 'cleanup': cleanupNow(); break;
    case 'save-settings': saveSettings(); break;
    case 'close-modal': closeModal(); break;
  }
});
$('archive-date').addEventListener('change',e=>selectDay(e.target.value));
$('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal()});

(async()=>{await poll();daySelected=statusCache?.today||localDate();fillSettings(settings);await loadRecent();setInterval(poll,1300);setInterval(async()=>{await loadRecent();if(currentPage==='archive')await loadArchive()},12000)})();
