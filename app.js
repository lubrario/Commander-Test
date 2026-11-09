'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = (value) => Number(value || 0).toLocaleString('pt-BR');
const money = (cents) => cents == null ? '—' : (cents / 100).toLocaleString('pt-BR', {style:'currency',currency:'BRL'});
const date = (s) => { if (!s) return '—'; const d = new Date(s); return isNaN(d.getTime()) ? s : d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'}); };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = (v) => {try{const u = new URL(v);return ['https:','http:'].includes(u.protocol) ? u.href : ''}catch{return ''}};
const SOURCE_KIND = {csv_feed:'Feed autorizado',csv_upload:'CSV importado',historical_reference:'Referência histórica'};
const LIMIT = 35;
let currentView = 'dashboard';
let page = 0;
let latest = null;
let snapshotsCache = [];
let searchRequestId = 0;
let debounceId;
let selectedFile = null;
const authKey = 'taberna-vault-admin';

function toast(message,type='info'){
  const host=$('#toast-container');const box=document.createElement('div');box.className='toast '+type;box.textContent=message;box.setAttribute('role','status');host.append(box);
  setTimeout(()=>box.remove(),6000);
}
function busy(button,active,message='Processando…'){
  if(active){button.dataset.oldtext=button.textContent;button.textContent=message;button.disabled=true}
  else{button.textContent=button.dataset.oldtext||button.textContent;button.disabled=false}
}
async function api(route,{method='GET',body}={}){
  const headers={};if(method!=='GET'){headers['Content-Type']='application/json';const token=sessionStorage.getItem(authKey);if(token)headers['X-Admin-Token']=token}
  const r=await fetch(route,{method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  let result;try{result=await r.json()}catch{throw Error('Resposta inesperada do servidor.')}
  if(!r.ok)throw Error(result.error||`Falha ${r.status}`);
  return result;
}
async function operation(btn,route,data,onDone){busy(btn,true);try{const r=await api(route,{method:'POST',body:data});if(onDone)onDone(r);await refresh();return r}catch(e){toast(e.message,'error');return null}finally{busy(btn,false)}}
function view(which){
  if(!$('#view-'+which))return;
  currentView=which;
  $$('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+which));
  $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===which));
  $('#breadcrumb-current').textContent=({'dashboard':'Visão geral','catalog':'Catálogo de singles','changes':'Alterações','commander':'Commander Lab','ligamagic':'LigaMagic · Taberna Geek','builder':'Deck inteligente','sources':'Fonte e importação'})[which]||which;
  $('#sidebar').classList.remove('shown');
  window.scrollTo({top:0,behavior:'smooth'});
  if(which==='catalog')search();
  if(which==='changes')loadChanges();
  if(which==='sources')renderHistory(snapshotsCache);
}
$$('[data-view]').forEach(x=>x.addEventListener('click',()=>view(x.dataset.view)));
$$('[data-open]').forEach(x=>x.addEventListener('click',()=>view(x.dataset.open)));
$('#menu-toggle').addEventListener('click',()=>$('#sidebar').classList.toggle('shown'));

function sourceState(s){
  const kind=s?.source_kind;
  const isHistoric=kind==='historical_reference';
  const label = !s?'Sem catálogo carregado':isHistoric?'Apenas dados históricos':`Catálogo: ${date(s.imported_at)}`;
  $('#top-state').innerHTML=`<span class="status-dot ${!s?'waiting':isHistoric?'warn':'good'}"></span><span>${esc(label)}</span>`;
  $('#source-mini').innerHTML=`<span class="status-dot ${!s?'waiting':isHistoric?'warn':'good'}"></span><div><strong>${s ? esc(SOURCE_KIND[kind]||'Importação') : 'Não configurada'}</strong><small>${s?esc(date(s.imported_at)):'Conecte um CSV autorizado'}</small></div>`;
  $('#date-meta').textContent=`Última importação: ${s?date(s.imported_at):'—'}`;
  $('#global-notice').innerHTML=s ? `<span class="notice-icon">${isHistoric?'!':'i'}</span><div><strong>${isHistoric?'Você está vendo dados históricos':'Estoque baseado no último arquivo importado'}</strong><p>${isHistoric?'Estas ofertas são referências antigas e não indicam cartas disponíveis hoje.':'A data de importação não prova que o estoque da loja estava atualizado naquele instante. Consulte a data de observação da fonte.'}</p></div><button class="text-link" data-open="sources">Ver origem ↗</button>` : `<span class="notice-icon">i</span><div><strong>Catálogo completo ainda não conectado</strong><p>Para conferências de cartas e substituições no Commander, use Deck inteligente (com chave ReefAPI). Para ver todo o catálogo, importe um CSV autorizado. Anúncios históricos nunca serão apresentados como estoque atual.</p></div><button class="text-link" data-open="sources">Configurar fonte ↗</button>`;
  $('#global-notice').querySelector('[data-open]').onclick=()=>view('sources');
}
function summary(s){
  $('#snapshot-type').textContent=s ? SOURCE_KIND[s.source_kind]||'Importado':'Sem dados';
  if(!s){$('#snapshot-summary').innerHTML='<div class="empty-emblem">◈</div><h3>O catálogo começa aqui</h3><p>Importe a primeira planilha para acompanhar preços, quantidades e histórico das singles.</p><button class="button button-outline" id="first-upload">Importar catálogo</button>';$('#snapshot-summary').className='empty-dashboard';$('#first-upload').onclick=()=>view('sources');return}
  $('#snapshot-summary').className='';
  $('#snapshot-summary').innerHTML=`<div class="summary-grid"><div><div class="info-label">Origem</div><div class="info-value subdued">${esc(SOURCE_KIND[s.source_kind]||'Arquivo')}</div></div><div><div class="info-label">Ofertas importadas</div><div class="info-value">${number(s.rows_count)}</div></div><div><div class="info-label">Identificador</div><div class="info-value">#${s.id}</div></div><div><div class="info-label">Consultado / importado em</div><div class="info-value subdued">${esc(date(s.imported_at))}</div></div></div><div class="summary-bottom"><small>${esc(s.source_label || 'Origem não informada')}${s.source_kind==='historical_reference'?' · NÃO é estoque atual':''}</small><button class="quiet-action" id="view-catalog-button">Pesquisar ofertas ↗</button></div>`;
  $('#view-catalog-button').onclick=()=>view('catalog');
}
function recent(snapshots){
  const short=snapshots.slice(0,3);
  $('#recent-snapshots').innerHTML=short.length?short.map(s=>`<div class="history-entry"><div class="timeline-icon">${s.source_kind==='csv_feed'?'↻':'▤'}</div><div class="timeline-content"><strong>${number(s.rows_count)} ofertas · ${esc(SOURCE_KIND[s.source_kind]||'CSV')}</strong><small>${date(s.imported_at)} · #${s.id}</small></div><span class="timeline-entry-badge">v${s.id}</span></div>`).join(''):'<div class="empty-list">Nenhuma consulta registrada ainda.</div>';
}
function renderHistory(snapshots){
  $('#snapshot-history').innerHTML=snapshots.length?snapshots.map(s=>`<div class="history-item"><div class="timeline-icon">${s.source_kind==='csv_feed'?'↻':'▤'}</div><div class="history-item-main"><strong>#${s.id} · ${number(s.rows_count)} ofertas · ${date(s.imported_at)}</strong><span title="${esc(s.source_label)}">${esc(s.source_label)}</span></div><div class="history-kind">${esc(SOURCE_KIND[s.source_kind]||'Importação')}</div></div>`).join(''):'<p class="muted">Nenhuma importação registrada.</p>';
}
async function refresh(){
  try{
    const data=await api('/api/status');latest=data.latest;
    const s=data.stats||{};
    for(const [id,value] of Object.entries({'metric-names':s.names,'metric-offers':s.offers,'metric-positive':s.positive,'metric-unknown':s.uncertain}))$('#'+id).textContent=value==null?'—':number(value);
    sourceState(latest);summary(latest);
    $('#feed-url').value=data.feed_url||'';
    $('#admin-panel').hidden=!data.auth_required;
    if(data.auth_required)$('#admin-token').value=sessionStorage.getItem(authKey)||'';
    const history=await api('/api/snapshots');snapshotsCache=history.items||[];
    recent(snapshotsCache);renderHistory(snapshotsCache);
    await Promise.all([search(),loadChanges()]);
  }catch(e){toast('Não foi possível carregar o sistema: '+e.message,'error')}
}

async function search(){
  const request=++searchRequestId;
  const q=$('#search-input').value.trim();const set=$('#search-set').value.trim();
  const params=new URLSearchParams({q,set,stock:$('#search-stock').checked?'1':'0',sort:$('#search-sort').value,limit:LIMIT,offset:page*LIMIT});
  try{
    const data=await api('/api/cards?'+params);
    if(request!==searchRequestId)return;
    const rows=data.items||[];const total=data.total||0;
    $('#catalog-count').textContent=number(total)+' ofertas encontradas';
    $('#page-info').textContent=`Página ${total?page+1:0} de ${Math.ceil(total/LIMIT)}`;
    $('#page-prev').disabled=page===0;
    $('#page-next').disabled=(page+1)*LIMIT>=total;
    $('#catalog-rows').innerHTML=rows.length?rows.map(r=>{
      const href=safeUrl(r.url);
      const qty=r.quantity==null?'<span class="qty-tag unknown">Não informado</span>':r.quantity>0?`<span class="qty-tag">${number(r.quantity)} un.</span>`:'<span class="qty-tag zero">0 un.</span>';
      return `<tr><td><div class="card-identity">${r.image_url?`<img class="card-thumb" src="${esc(safeUrl(r.image_url))}" alt="" loading="lazy" referrerpolicy="no-referrer" />`:'<span class="card-placeholder">✦</span>'}<div class="card-name"><strong>${esc(r.name)}</strong><span class="sub-line">${esc(r.type_line||r.finish||'Magic: The Gathering')}</span></div></div></td><td>${esc(r.set_code||'—')}${r.collector_number?`<span class="sub-line">#${esc(r.collector_number)}</span>`:''}</td><td>${esc([r.language,r.condition].filter(Boolean).join(' · ')||'—')}<span class="sub-line">${esc(r.finish||'')}</span></td><td class="money">${money(r.price_cents)}</td><td>${qty}</td><td>${href?`<a class="link-arrow" href="${esc(href)}" rel="noopener noreferrer" target="_blank">Ver anúncio ↗</a>`:'—'}</td></tr>`;
    }).join(''):`<tr><td colspan="6" class="table-empty">${latest?'Nenhuma carta corresponde aos filtros.':'Importe um catálogo em Fonte e importação para começar.'}</td></tr>`;
    $('#catalog-disclaimer').textContent=latest?.source_kind==='historical_reference'?'ATENÇÃO: as ofertas exibidas são referências históricas, não estoque atual.':'Quantidade positiva significa apenas unidades registradas no último CSV, não confirmação de disponibilidade ao vivo.';
  }catch(e){if(request===searchRequestId)toast(e.message,'error')}
}
function resetSearch(){page=0;search()}
$('#search-input').addEventListener('input',()=>{clearTimeout(debounceId);page=0;debounceId=setTimeout(search,220)});
$('#search-set').addEventListener('input',()=>{clearTimeout(debounceId);page=0;debounceId=setTimeout(search,220)});
$('#search-sort').addEventListener('change',resetSearch);
$('#search-stock').addEventListener('change',resetSearch);
$('#page-prev').addEventListener('click',()=>{if(page>0){page--;search()}});
$('#page-next').addEventListener('click',()=>{page++;search()});
async function loadChanges(){
  try{
    const data=await api('/api/changes');const items=data.items||[];
    const counts={entrada:0,saida:0,alteracao:0};items.forEach(r=>counts[r.category]=(counts[r.category]||0)+1);
    $('#change-new').textContent=number(counts.entrada);$('#change-gone').textContent=number(counts.saida);$('#change-updated').textContent=number(counts.alteracao);
    $('#changes-rows').innerHTML=items.length?items.slice(0,250).map(r=>`<tr><td><span class="change-badge ${esc(r.category)}">${esc({entrada:'Entrada',saida:'Saída',alteracao:'Alteração'}[r.category]||r.category)}</span></td><td><strong>${esc(r.name)}</strong></td><td>${esc(r.set_code||'—')}</td><td>${money(r.before?.price_cents)} → ${money(r.after?.price_cents)}</td><td>${r.before?.quantity==null?'—':number(r.before.quantity)} → ${r.after?.quantity==null?'—':number(r.after.quantity)}</td></tr>`).join(''):'<tr><td colspan="5" class="table-empty">Ainda não existem diferenças ou há menos de duas importações.</td></tr>';
  }catch(e){toast(e.message,'error')}
}
function fileSelected(f){
  selectedFile=f||null;$('#file-name').textContent=f?f.name:'Clique para selecionar um CSV';
  if(f&& !/\.(csv|txt)$/i.test(f.name))toast('Confira se o arquivo contém dados CSV.','error');
}
$('#csv-file').addEventListener('change',e=>fileSelected(e.target.files?.[0]));
const drop=$('#dropzone');
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('drag')});
drop.addEventListener('dragleave',()=>drop.classList.remove('drag'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('drag');fileSelected(e.dataTransfer.files?.[0])});
$('#upload-button').addEventListener('click',async()=>{
  if(!selectedFile)return toast('Selecione um arquivo CSV para importar.','error');
  if(selectedFile.size>12*1024*1024)return toast('O CSV não pode exceder 12 MB.','error');
  const text=await selectedFile.text();
  const r=await operation($('#upload-button'),'/api/upload',{csv:text,filename:selectedFile.name,historical:$('#csv-historical').checked},r=>toast(`Snapshot #${r.snapshot_id} salvo: ${number(r.rows)} ofertas importadas.`,'success'));
  if(r){selectedFile=null;$('#csv-file').value='';fileSelected(null)}
});
$('#save-feed').addEventListener('click',()=>operation($('#save-feed'),'/api/feed',{url:$('#feed-url').value.trim()},()=>toast('Endereço do feed salvo. A consulta ocorre somente quando você solicitar.','success')));
const sync=async(btn)=>{
  if(!$('#feed-url').value.trim()){if(builderLive){view('builder');toast('A busca ao vivo funciona por carta no Deck inteligente. Não há feed completo para sincronizar.','info')}else{view('sources');toast('Nenhum feed CSV configurado. Consulte Deck inteligente ou importe um CSV.','error')}return}
  await operation(btn,'/api/sync',{},r=>toast(`Consulta concluída: ${number(r.rows)} ofertas importadas. Confira a data da fonte.`,'success'));
};
$('#sync-button').addEventListener('click',()=>sync($('#sync-button')));
$('#top-sync').addEventListener('click',()=>sync($('#top-sync')));
$('#save-token').addEventListener('click',()=>{const v=$('#admin-token').value.trim();if(v)sessionStorage.setItem(authKey,v);else sessionStorage.removeItem(authKey);toast(v?'Chave aplicada nesta sessão.':'Chave retirada da sessão.','success')});
$('#enrich-button').addEventListener('click',()=>operation($('#enrich-button'),'/api/enrich',{limit:225},r=>toast(`Scryfall: ${r.received} cartas enriquecidas; ${r.not_found||0} não identificadas.`,'success')));
$$('[data-commander]').forEach(el=>el.addEventListener('click',()=>$('#commander-input').value=el.dataset.commander));
$('#commander-input').addEventListener('keydown',e=>{if(e.key==='Enter')$('#commander-check').click()});
$('#commander-check').addEventListener('click',async()=>{
  const name=$('#commander-input').value.trim();if(!name)return toast('Informe um nome de comandante.','error');
  const btn=$('#commander-check');busy(btn,true,'Consultando Scryfall…');
  try{
    const data=await api('/api/commander',{method:'POST',body:{name}});
    const c=data.commander;const good=data.compatible||[];
    const colors=c.colors?.length?c.colors:['C'];
    $('#commander-results').className='';
    $('#commander-results').innerHTML=`<div class="commander-result-header">${safeUrl(c.image_url)?`<img class="commander-image" src="${esc(safeUrl(c.image_url))}" alt="Arte da carta ${esc(c.name)}" loading="lazy"/>`:'<div class="empty-emblem">♜</div>'}<div><h3>${esc(c.name)}</h3><p>${esc(c.type_line||'Carta de Magic')}</p><p>Legalidade Commander: ${esc(c.legal_commander||'Desconhecida')}</p><div class="mana-row">${colors.map(color=>`<span class="mana mana-${esc(color)}">${esc(color)}</span>`).join('')}</div></div></div><div class="commander-statline"><div><strong>${number(good.length)}</strong><small>Ofertas com cores e legalidade compatíveis</small></div><div><strong>${number(data.unverified||0)}</strong><small>Ofertas sem metadados suficientes</small></div></div><p class="section-note">${latest?.source_kind==='historical_reference'?'Dados históricos, não disponibilidade atual.':'Disponibilidade depende da última importação.'}</p>`;
    $('#compatible-panel').hidden=false;
    $('#compatible-total').textContent=number(good.length)+' ofertas';
    $('#compatible-rows').innerHTML=good.length?good.slice(0,150).map(r=>`<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.set_code||'—')}</td><td>${esc(r.type_line||'—')}</td><td class="money">${money(r.price_cents)}</td><td>${r.quantity==null?'—':number(r.quantity)}</td></tr>`).join(''):'<tr><td colspan="5" class="table-empty">Nenhuma oferta compatível comprovada. Importe catálogo e enriqueça metadados via Scryfall.</td></tr>';
    if(c.legal_commander!=='legal')toast('A carta escolhida pode não ser legal no Commander.','error');
  }catch(e){toast(e.message,'error');$('#commander-results').textContent=e.message}finally{busy(btn,false)}
});
refresh();


// Plano gratuito: backup completo sob demanda, acessível apenas com chave administrativa.
$('#download-backup').addEventListener('click',async()=>{
  const btn=$('#download-backup');busy(btn,true,'Gerando backup…');
  try{
    const headers={};const token=sessionStorage.getItem(authKey);if(token)headers['X-Admin-Token']=token;
    const response=await fetch('/api/backup.zip',{headers,cache:'no-store'});
    if(!response.ok){let msg='Não foi possível baixar o backup.';try{msg=(await response.json()).error||msg}catch{}throw Error(msg)}
    const blob=await response.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download='taberna-vault-backup-'+new Date().toISOString().slice(0,10)+'.zip';document.body.appendChild(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),3000);
    toast('Backup baixado. Guarde o ZIP em um local seguro.','success');
  }catch(e){toast(e.message,'error')}finally{busy(btn,false)}
});
$('#restore-backup').addEventListener('click',async()=>{
  const file=$('#restore-file').files?.[0];
  if(!file)return toast('Selecione o backup ZIP para restaurar.','error');
  if(file.size>24*1024*1024)return toast('Backup acima de 24 MB.','error');
  if(!confirm('Atenção: restaurar este backup substitui todos os dados atualmente no site. Continuar?'))return;
  const btn=$('#restore-backup');busy(btn,true,'Restaurando…');
  try{
    const headers={'Content-Type':'application/zip'};
    const token=sessionStorage.getItem(authKey);if(token)headers['X-Admin-Token']=token;
    const response=await fetch('/api/restore',{method:'POST',headers,body:file,cache:'no-store'});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Erro na restauração.');
    toast(`Backup restaurado: ${number(result.snapshots)} versões e ${number(result.offers)} ofertas.`,'success');
    $('#restore-file').value='';await refresh();
  }catch(e){toast(e.message,'error')}finally{busy(btn,false)}
});


// LigaMagic: ponte de consulta assistida, sem prometer coleta automatizada.
const LIGA_STORE_ID = '41369';
const LIGA_STORE_URL = 'https://www.ligamagic.com.br/?view=mp/showcase/home&id=41369';
const LIGA_STORAGE = 'taberna-vault-ligamagic-41369-v1';
const LIGA_LIST_STORAGE = 'taberna-vault-ligamagic-decklist-v1';
let ligaCards = [];
function ligaRead() {
  try { const v=JSON.parse(localStorage.getItem(LIGA_STORAGE)||'{}');return v&&typeof v==='object'&&!Array.isArray(v)?v:{}; }
  catch{return {}}
}
function ligaSave(v){try{localStorage.setItem(LIGA_STORAGE,JSON.stringify(v));return true}catch{toast('O navegador não permitiu salvar. Exporte o CSV para preservar seus dados.','error');return false}}
function ligaKey(name){return name.trim().toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g,'')}
function ligaCardUrl(name){return 'https://www.ligamagic.com.br/?view=cards/search&card='+encodeURIComponent(name)}
function ligaParse(input){
  const output=new Map();
  const lines=input.replace(/\r/g,'').split('\n').slice(0,500);
  for(let line of lines){
    line=line.trim();if(!line||/^\/\//.test(line)||/^#/.test(line)||/^(commander|mainboard|sideboard|maybeboard|criaturas|terrenos|instantes|feitiços|artefatos|encantamentos|planeswalkers|deck|main):?$/i.test(line))continue;
    line=line.replace(/\s+\/\/.*$/,'').trim();
    let count=1, name=line;
    const m=line.match(/^(\d{1,3})\s*[x×]?\s+(.+)$/i);
    if(m){count=Number(m[1]);name=m[2]}
    else {const n=line.match(/^(.+?)\s+[x×]\s*(\d{1,3})$/i);if(n){name=n[1];count=Number(n[2])}}
    name=name.replace(/\s+\(([A-Z0-9]{2,8})\)(?:\s+[A-Za-z0-9★-]{1,8})?\s*$/,'').replace(/\s+\[[A-Z0-9]{2,8}\](?:\s+[A-Za-z0-9★-]{1,8})?\s*$/,'').trim();
    if(!count||!name||name.length>180)continue;
    const key=ligaKey(name);if(output.has(key))output.get(key).need+=count;else output.set(key,{name,need:count});
    if(output.size>=150)break;
  }
  return [...output.values()];
}
function ligaRender(){
  const saved=ligaRead();let reviewed=0, priced=0, complete=0, cost=0;
  const html=ligaCards.map((r,i)=>{
    const item=saved[ligaKey(r.name)]||{};
    const reviewedNow=!!item.observed_at;
    if(reviewedNow){reviewed++;if(item.price!=null){priced++;cost+=Number(item.price)*r.need};if(item.quantity!=null&&item.quantity>=r.need)complete++}
    const status=reviewedNow?new Date(item.observed_at).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'}):'Pendente';
    return `<tr><td><strong>${esc(r.name)}</strong><span class="sub-line">Precisa: ${number(r.need)} un.</span></td><td><a class="link-arrow" href="${esc(ligaCardUrl(r.name))}" target="_blank" rel="noopener noreferrer">Buscar carta ↗</a></td><td><input class="liga-cell-input" type="text" inputmode="decimal" data-liga-price="${i}" aria-label="Preço unitário de ${esc(r.name)}" value="${item.price==null?'':esc(item.price)}" placeholder="Ex.: 2,50" /></td><td><input class="liga-cell-input" type="number" inputmode="numeric" min="0" step="1" data-liga-qty="${i}" aria-label="Estoque de ${esc(r.name)}" value="${item.quantity==null?'':esc(item.quantity)}" placeholder="—" /></td><td><span class="${reviewedNow?'liga-checked':'liga-pending'}">${esc(status)}</span></td><td><button class="page-button" data-liga-save="${i}">Salvar</button></td></tr>`;
  }).join('');
  $('#liga-rows').innerHTML=html||'<tr><td class="table-empty" colspan="6">Nenhuma carta detectada. Cole uma lista válida.</td></tr>';
  $('#liga-progress').textContent=`${reviewed} / ${ligaCards.length} conferidas`;
  $('#liga-summary').textContent=ligaCards.length?`${ligaCards.length} cartas diferentes · ${reviewed} revisadas · ${priced} com preço · ${complete} com estoque suficiente informado · Subtotal conhecido: ${cost.toLocaleString('pt-BR',{style:'currency',currency:'BRL'})} (não inclui cartas sem preço)`:'Cole uma lista e clique em Preparar buscas.';
}
$('#liga-decklist').value=(()=>{try{return localStorage.getItem(LIGA_LIST_STORAGE)||''}catch{return ''}})();
$('#liga-build').addEventListener('click',()=>{
  const input=$('#liga-decklist').value.trim();
  ligaCards=ligaParse(input);
  try{localStorage.setItem(LIGA_LIST_STORAGE,input)}catch{}
  ligaRender();
  if(!ligaCards.length)toast('A lista está vazia ou não contém nomes válidos.','error');
  else toast(`${ligaCards.length} nomes preparados. Abra a busca de cada carta e confirme a loja 41369.`,'success');
});
$('#liga-copy').addEventListener('click',async()=>{
  const text=$('#liga-decklist').value.trim();if(!text)return toast('Cole primeiro uma lista.','error');
  try{await navigator.clipboard.writeText(text);toast('Lista copiada para a área de transferência.','success')}catch{toast('Não foi possível copiar automaticamente. Selecione o texto e copie.','error')}
});
$('#liga-rows').addEventListener('click',e=>{
  const button=e.target.closest('[data-liga-save]');if(!button)return;
  const i=Number(button.dataset.ligaSave), card=ligaCards[i];if(!card)return;
  const p=$(`[data-liga-price="${i}"]`).value.trim(),q=$(`[data-liga-qty="${i}"]`).value.trim();
  if(!p&&!q)return toast('Informe preço ou quantidade observados na oferta da Taberna Geek.','error');
  const normalizedPrice=p.replace(',', '.');const pn=p===''?null:Number(normalizedPrice),qn=q===''?null:Number(q);
  if((pn!==null&&(!/^\d+(?:[.,]\d{1,2})?$/.test(p)||!Number.isFinite(pn)||pn<0))||(qn!==null&&(!Number.isInteger(qn)||qn<0)))return toast('Confira os valores de preço e quantidade.','error');
  const saved=ligaRead();saved[ligaKey(card.name)]={name:card.name,price:pn,quantity:qn,observed_at:new Date().toISOString(),source:'Conferência manual na LigaMagic; loja 41369'};
  if(ligaSave(saved)){ligaRender();toast('Conferência manual salva neste navegador.','success')}
});
$('#liga-export').addEventListener('click',()=>{
  if(!ligaCards.length)return toast('Prepare primeiro uma lista.','error');
  const saved=ligaRead();
  const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const lines=[['nome','quantidade_necessaria','preco_unitario_reais','quantidade_observada','observado_em','origem','link_busca_ligamagic','loja_id','status'].map(quote).join(';')];
  for(const row of ligaCards){
    const data=saved[ligaKey(row.name)];const vals=[row.name,row.need,data?.price==null?'':String(data.price).replace('.',','),data?.quantity??'',data?.observed_at||'',data?.source||'',ligaCardUrl(row.name),LIGA_STORE_ID,data?'conferido_manual':'nao_conferido'];
    lines.push(vals.map(quote).join(';'));
  }
  const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='taberna-ligamagic-conferencias-'+new Date().toISOString().slice(0,10)+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast('CSV de conferências manuais baixado. Não é o estoque completo.','success');
});
$('#liga-clear').addEventListener('click',()=>{
  if(!confirm('Apagar todas as conferências manuais guardadas neste navegador? A lista de cartas será mantida.'))return;
  try{localStorage.removeItem(LIGA_STORAGE)}catch{}
  ligaRender();toast('Conferências apagadas.','success');
});
if($('#liga-decklist').value.trim())ligaCards=ligaParse($('#liga-decklist').value);
ligaRender();

// ===== V5: Commander inteligente, verificação por carta e substituição funcional =====
// Nenhum segredo ReefAPI é enviado ao navegador; apenas o token de administrador
// é usado para permitir consultas autenticadas no servidor.
const BUILDER_STORE = 'taberna-vault-builder-v5';
const BUILDER_LIMIT = 10;
const BUILDER_CALL_LIMIT = 30;
let builderRunning = false;
let builderStopRequested = false;
let builderResults = [];
let builderChecks = 0;
let builderLive = false;
function builderLoad(){
  try{
    const data=JSON.parse(localStorage.getItem(BUILDER_STORE)||'{}');
    if(data && typeof data==='object' && Array.isArray(data.rows)){
      builderResults=data.rows.slice(0,200);
      $('#builder-commander').value=String(data.commander||'').slice(0,180);
      $('#builder-decklist').value=String(data.decklist||'').slice(0,20000);
    }
  }catch{}
  builderRender();
}
function builderPersist(){
  try{localStorage.setItem(BUILDER_STORE,JSON.stringify({
    rows:builderResults,commander:$('#builder-commander').value,decklist:$('#builder-decklist').value,
    stored_at:new Date().toISOString()
  }))}catch{toast('Não foi possível preservar a lista no navegador. Exporte o relatório.','error')}
}
async function builderConnection(){
  try{
    const d=await api('/api/builder/status');
    builderLive=!!d.live_enabled;
    $('#builder-connection').textContent=d.live_enabled ? 'Fonte ao vivo habilitada: ReefAPI → LigaMagic → Taberna Geek #41369' : d.snapshot_available ? 'Fonte ao vivo desligada; utilizando somente o catálogo importado' : 'Ainda sem fonte para verificar disponibilidade';
    $('#builder-connection-detail').textContent=d.live_enabled ? 'Cada carta consultada exige créditos da ReefAPI. A consulta confirma uma oferta anunciada na loja, não reserva a carta. A chave fica apenas no servidor.' : d.snapshot_available ? 'As verificações refletem a data do CSV, não o estoque de hoje. Para busca ao vivo, cadastre REEF_API_KEY nas variáveis do Render.' : 'Para conferir anúncios atuais automaticamente, cadastre REEF_API_KEY nas variáveis do Render. A ReefAPI oferece um teste gratuito com limite de créditos.';
  }catch(e){$('#builder-connection').textContent='Falha na configuração: '+e.message}
}
function builderStatus(text){$('#builder-status').textContent=text}
function builderRender(){
  const added=builderResults.filter(r=>r.kind==='original'||r.kind==='substitute');
  const replaced=added.filter(r=>r.kind==='substitute');
  const total=added.reduce((s,r)=>s+(Number(r.price_cents||0)*Number(r.needed||1)),0);
  $('#builder-stat-added').textContent=added.length+' adicionadas';
  $('#builder-stat-replaced').textContent=replaced.length+' substituídas';
  $('#builder-stat-checks').textContent=builderChecks+' verificações nesta execução';
  $('#builder-stat-total').textContent=money(total);
  $('#builder-rows').innerHTML=builderResults.length?builderResults.map(r=>{
    const good=r.kind==='original'||r.kind==='substitute';
    const cls=good?'ok':r.kind==='unknown'?'warn':'error';
    const title=r.kind==='substitute'?'SUBSTITUIÇÃO':r.kind==='original'?'NA LISTA':r.kind==='unknown'?'NÃO CONFIRMADO':'SEM OPÇÃO CONFIRMADA';
    const url=safeUrl(r.url||'');
    return `<tr><td><strong>${esc(r.original)}</strong><span class="builder-subline">${number(r.needed||1)} un.</span></td>
      <td>${good?`<strong>${esc(r.selected)}</strong>${r.kind==='substitute'?`<span class="builder-subline">Substitui ${esc(r.original)}</span>`:''}`:'<span class="muted">—</span>'}<span class="builder-state ${cls}">${title}</span></td>
      <td><span class="builder-subline">${esc(r.reason||'—')}</span>${url?`<a class="link-arrow" href="${esc(url)}" rel="noopener noreferrer" target="_blank">Ver oferta ↗</a>`:''}</td>
      <td class="money">${good?money(r.price_cents):'—'}</td>
      <td><span class="builder-subline">${esc(r.source||'—')}</span><span class="builder-subline">${esc(date(r.checked_at))}</span></td></tr>`;
  }).join(''):'<tr><td colspan="5" class="table-empty">Nenhuma consulta iniciada.</td></tr>';
}
function builderExtract(result,entry,kind,reason){
  const selected=kind==='substitute'?result.card:entry.name;
  return {kind,original:entry.name,needed:entry.need,selected,
    reason,price_cents:result.price_cents,quantity:result.quantity,
    source:result.source||'',checked_at:result.checked_at||'',
    url:(result.offer||{}).url||'',source_kind:result.source_kind||'unknown'};
}
async function builderAvailability(card,qty,cache){
  const cacheKey=ligaKey(card)+'|'+qty;
  if(cache.has(cacheKey))return cache.get(cacheKey);
  if(builderChecks>=BUILDER_CALL_LIMIT)throw Error('Limite de 30 consultas por execução atingido. Exporte a lista e inicie outra rodada.');
  builderChecks++;
  const result=await api('/api/builder/availability',{method:'POST',body:{card,quantity:qty}});
  cache.set(cacheKey,result);
  $('#builder-stat-checks').textContent=builderChecks+' verificações nesta execução';
  return result;
}
async function builderOne(entry,commander,allNames,usedNames,cache){
  builderStatus(`Conferindo ${entry.name} na Taberna Geek…`);
  const wanted=await builderAvailability(entry.name,entry.need,cache);
  if(wanted.status==='available'||wanted.status==='snapshot_available'){
    return builderExtract(wanted,entry,'original',wanted.status==='snapshot_available'?'Oferta presente no último CSV, sem verificação ao vivo.':'Carta original anunciada pela loja na consulta ao vivo.');
  }
  if(!['not_listed','snapshot_not_found'].includes(wanted.status)){
    return builderExtract(wanted,entry,'unknown',wanted.message||'A fonte não confirmou a disponibilidade da carta.');
  }
  if(builderStopRequested)return builderExtract(wanted,entry,'unknown','Consulta interrompida pelo usuário.');
  builderStatus(`${entry.name} não teve oferta identificada. Procurando sinergias com ${commander}…`);
  const proposals=await api('/api/builder/candidates',{method:'POST',body:{card:entry.name,commander}});
  if(!proposals.candidates?.length){
    return builderExtract(wanted,entry,'unknown','Nenhuma alternativa funcional foi identificada; '+(proposals.notice||''));
  }
  let inconclusive=0;
  for(const candidate of proposals.candidates){
    if(builderStopRequested)break;
    if(builderChecks>=BUILDER_CALL_LIMIT)break;
    if(allNames.has(ligaKey(candidate.name))||usedNames.has(ligaKey(candidate.name)))continue;
    builderStatus(`${entry.name} → verificando substituta ${candidate.name} na loja…`);
    const checked=await builderAvailability(candidate.name,entry.need,cache);
    if(checked.status==='available'||checked.status==='snapshot_available'){
      usedNames.add(ligaKey(candidate.name));
      return builderExtract(checked,entry,'substitute',
        `${candidate.reason}. ${checked.status==='snapshot_available'?'Oferta no CSV importado, não confirmada ao vivo.':'Substituta com oferta encontrada na loja.'}`);
    }
    if(['unknown','unconfigured'].includes(checked.status))inconclusive++;
  }
  return builderExtract(wanted,entry,'unknown',builderChecks>=BUILDER_CALL_LIMIT?'Limite de verificações atingido antes de confirmar uma substituta.':
    builderStopRequested?'Pesquisa interrompida.':inconclusive?'Alternativas encontradas, mas parte do estoque não pôde ser confirmado.':'Nenhuma substituta com oferta confirmada foi encontrada entre as candidatas analisadas.');
}
$('#builder-start').addEventListener('click',async()=>{
  if(builderRunning)return;
  const commander=$('#builder-commander').value.trim();
  const list=ligaParse($('#builder-decklist').value);
  if(!commander||commander.length>180)return toast('Informe o nome do comandante.','error');
  if(!list.length)return toast('Cole pelo menos uma carta na lista.','error');
  if(list.length>BUILDER_LIMIT)return toast(`Para controlar os créditos, selecione até ${BUILDER_LIMIT} cartas por execução. Divida uma decklist grande em lotes.`,'error');
  if(list.some(r=>r.need>100))return toast('Quantidade máxima de 100 por linha.','error');
  const isLive=builderLive;
  if(!isLive&&!confirm('Sem chave ReefAPI configurada: o sistema usará apenas o último CSV importado (se existir). Continuar sem verificação ao vivo?'))return;
  if(isLive&&!confirm(`Você vai verificar até ${list.length} cartas e possíveis substitutas, com limite de ${BUILDER_CALL_LIMIT} chamadas pagas em créditos da ReefAPI (até ${BUILDER_CALL_LIMIT*4} créditos). Continuar?`))return;
  builderRunning=true;builderStopRequested=false;builderChecks=0;
  const start=$('#builder-start');start.disabled=true;
  $('#builder-stop').disabled=false;
  builderResults=[];builderRender();builderPersist();
  const allNames=new Set(list.map(x=>ligaKey(x.name)));allNames.add(ligaKey(commander));
  const usedNames=new Set();const cache=new Map();
  let stoppedForError=false;
  try{
    for(let i=0;i<list.length;i++){
      if(builderStopRequested)break;
      if(builderChecks>=BUILDER_CALL_LIMIT){builderStatus('Limite de verificações atingido.');break;}
      $('#builder-progress').textContent=`${i+1}/${list.length}`;
      try{
        const result=await builderOne(list[i],commander,allNames,usedNames,cache);
        builderResults.push(result);
        if(result.kind==='original')usedNames.add(ligaKey(result.selected));
        builderRender();builderPersist();
      }catch(e){
        const msg=e.message||'Falha na consulta';
        builderResults.push({kind:'unknown',original:list[i].name,needed:list[i].need,selected:'',reason:msg,source:'Erro de consulta',checked_at:new Date().toISOString()});
        builderRender();builderPersist();
        builderStatus('Consulta interrompida: '+msg);
        stoppedForError=true;
        break; // Evitar gastar mais créditos após um erro na fonte.
      }
    }
    if(!stoppedForError)builderStatus(builderStopRequested?'Interrompido. Resultados anteriores foram preservados.':
      `Busca concluída. ${builderResults.filter(r=>r.kind==='original'||r.kind==='substitute').length} cartas selecionadas. Confirme as ofertas antes de comprar.`);
  }finally{
    builderRunning=false;
    start.disabled=false;$('#builder-stop').disabled=true;
    $('#builder-progress').textContent=`${builderResults.length}/${list.length} analisadas`;
    builderPersist();
  }
});
$('#builder-stop').addEventListener('click',()=>{builderStopRequested=true;builderStatus('Parando após a consulta em andamento…')});
function builderDownload(name,content,type){
  const url=URL.createObjectURL(new Blob([content],{type}));
  const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);
}
$('#builder-export').addEventListener('click',()=>{
  const selected=builderResults.filter(r=>r.kind==='original'||r.kind==='substitute');
  if(!selected.length)return toast('Nenhuma carta foi selecionada ainda.','error');
  builderDownload('taberna-commander-selecionadas.txt',selected.map(r=>`${r.needed} ${r.selected}`).join('\n')+'\n','text/plain;charset=utf-8');
});
$('#builder-export-csv').addEventListener('click',()=>{
  if(!builderResults.length)return toast('Ainda não existem resultados para exportar.','error');
  const quoteCSV=x=>'"'+String(x??'').replace(/"/g,'""')+'"';
  const head=['original','quantidade','selecionada','situacao','motivo','preco_unitario_reais','quantidade_na_loja','fonte','consulta_em','url'];
  const lines=[head,...builderResults.map(r=>[r.original,r.needed,r.selected,r.kind,r.reason,r.price_cents==null?'':(r.price_cents/100).toFixed(2).replace('.',','),r.quantity??'',r.source,r.checked_at,r.url])];
  builderDownload('taberna-commander-relatorio.csv','\ufeff'+lines.map(x=>x.map(quoteCSV).join(';')).join('\r\n'),'text/csv;charset=utf-8');
});
$('#builder-clear').addEventListener('click',()=>{
  if(builderRunning)return toast('Pare a consulta antes de limpar.','error');
  if(!confirm('Apagar os resultados salvos neste navegador?'))return;
  builderResults=[];builderChecks=0;builderRender();builderPersist();
  $('#builder-progress').textContent='Aguardando';builderStatus('Resultados limpos.');
});
builderLoad();builderConnection();
