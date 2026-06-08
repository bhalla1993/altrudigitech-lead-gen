async function fetchLeads(){
  const res = await fetch('/leads');
  if(!res.ok){console.error('Failed to fetch leads'); return []}
  return res.json();
}

function formatDate(s){ try{ return new Date(s).toLocaleString() }catch(e){return s} }

function clearTable(){ document.querySelector('#leads-table tbody').innerHTML = '' }

// track currently shown detail lead id (for toggle/collapse)
let currentDetailId = null;

function showDetail(lead){
  const detailEl = document.getElementById('detail');
  // toggle: collapse if same row clicked twice
  if(currentDetailId === lead.id){
    // hide details and revoke ephemeral images
    const d = document.getElementById('img-desktop');
    const m = document.getElementById('img-mobile');
    if(d && d.dataset && d.dataset.objectUrl){ URL.revokeObjectURL(d.dataset.objectUrl); delete d.dataset.objectUrl; }
    if(m && m.dataset && m.dataset.objectUrl){ URL.revokeObjectURL(m.dataset.objectUrl); delete m.dataset.objectUrl; }
    detailEl.style.display = 'none';
    currentDetailId = null;
    return;
  }
  currentDetailId = lead.id;
  detailEl.style.display = 'block';
  const meta = document.getElementById('detail-meta');
  const contacted = lead.contacted ? '<span class="pill">Contacted</span>' : '<span class="pill">Uncontacted</span>';
  meta.innerHTML = `<div><strong>${lead.business_name || ''}</strong> — <a href="${lead.website_url}" target="_blank">${lead.website_url}</a> ${contacted}</div><div class="small muted">Score: ${lead.score || ''} • ${formatDate(lead.created_at)}</div>`;
  // audit info
  const auditDiv = document.getElementById('detail-audit');
  auditDiv.innerHTML = '';
  if(lead.audit_status){
    const status = lead.audit_status;
    const btn = `<a href="/leads/${lead.id}/audit/latest" target="_blank" class="pill">Open audit JSON</a>`;
    auditDiv.innerHTML = `<strong>Audit:</strong> <span class="small muted">${status}</span> ${btn}`;
    // try to fetch and summarize
    fetch('/leads/' + lead.id + '/audit/latest').then(r=>{
      if(!r.ok) throw new Error('no audit');
      return r.json();
    }).then(j=>{
      if(j.error){
        auditDiv.innerHTML += `<div class="small muted">Audit error: ${j.error}</div>`;
        return;
      }
      // lighthouse format: categories with score 0..1
      if(j.categories){
        const parts = [];
        for(const k of Object.keys(j.categories)){
          const v = j.categories[k] && j.categories[k].score ? Math.round(j.categories[k].score*100) : null;
          if(v !== null) parts.push(`${k}: ${v}`);
        }
        if(parts.length) auditDiv.innerHTML += `<div class="small muted">Scores — ${parts.join(' • ')}</div>`;
      }
    }).catch(()=>{
      // ignore
    });
  } else {
    auditDiv.innerHTML = `<span class="small muted">No audit run</span>`;
  }
  const d = document.getElementById('img-desktop');
  const m = document.getElementById('img-mobile');
  const catDiv = document.getElementById('detail-categories');
  // render deterministic categories if available in features_json
  catDiv.innerHTML = '';
  try{
    if(lead.features_json){
      let fj = typeof lead.features_json === 'string' ? JSON.parse(lead.features_json) : lead.features_json;
      const cats = fj && fj.categories ? fj.categories : null;
      if(cats){
        const parts = [];
        for(const k of Object.keys(cats)){
          parts.push(`<span class="cat-pill">${k}: ${cats[k]}</span>`);
        }
        if(parts.length){
          catDiv.innerHTML = `<strong>Categories:</strong> <div class="cat-list">${parts.join(' ')}</div>`;
        }
      }
    }
  }catch(e){
    // ignore parse errors
  }
  function normalizePath(p){
    if(!p) return '';
    let s = String(p).trim();
    // remove Python-style repr wrappers like "u'...'", "b'...'", or surrounding quotes
    if((s.startsWith("u'") || s.startsWith("b'")) && s.endsWith("'")){
      s = s.slice(2, -1);
    }
    if((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))){
      s = s.slice(1, -1);
    }
    return s;
  }
  function buildImageUrl(p){
    const s = normalizePath(p);
    if(!s) return '';
    if(s.startsWith('/')) return s;
    if(s.startsWith('data/')) return '/' + s; // already includes data/
    if(s.startsWith('screenshots/')) return '/data/' + s;
    // fallback: assume it's under data/
    return '/data/' + s;
  }
  function setImgWithFallback(imgElem, originalPath){
    if(!originalPath){ imgElem.src = ''; return; }
    const s = normalizePath(originalPath);
    if(!s){ imgElem.src = ''; return; }
    const variants = [];
    // primary
    variants.push(buildImageUrl(s));
    // without leading slash
    if(variants[0].startsWith('/')) variants.push(variants[0].slice(1));
    // raw path prefixed with /data/
    if(!s.startsWith('data/')) variants.push('/data/' + s);
    // ensure data/screenshots fallback
    const base = s.split('/').pop();
    if(base){
      variants.push('/data/screenshots/' + base);
      variants.push('/data/' + base);
    }
    // try variants sequentially on error
    let idx = 0;
    imgElem.onerror = function(){
      idx += 1;
      if(idx >= variants.length){ imgElem.style.display = 'none'; return; }
      imgElem.src = variants[idx];
    };
    imgElem.src = variants[0];
  }

  // Load ephemeral screenshots on demand (streamed from server, not persisted)
  async function loadEphemeralScreenshot(imgElem, leadId, device){
    // revoke any previous object URL
    if(imgElem && imgElem.dataset && imgElem.dataset.objectUrl){
      try{ URL.revokeObjectURL(imgElem.dataset.objectUrl); }catch(e){}
      delete imgElem.dataset.objectUrl;
    }
    if(!imgElem) return;
    imgElem.style.display = '';
    imgElem.src = '';
    try{
      const res = await fetch('/leads/' + leadId + '/screenshot?device=' + device);
      if(!res.ok) throw new Error('screenshot fetch failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      imgElem.src = url;
      imgElem.dataset.objectUrl = url;
    }catch(e){
      console.error('Failed to load ephemeral screenshot', e);
      imgElem.style.display = 'none';
    }
  }

  loadEphemeralScreenshot(d, lead.id, 'desktop');
  loadEphemeralScreenshot(m, lead.id, 'mobile');
  // generate email button wiring
  const genBtn = document.getElementById('gen-email-btn');
  const previewEl = document.getElementById('email-preview');
  if(genBtn){
    genBtn.disabled = false;
    genBtn.onclick = (ev)=>{
      ev.stopPropagation();
      generateEmailPreview(lead.id);
    };
    // hide preview when showing a different lead
    previewEl.style.display = 'none';
    previewEl.innerHTML = '';
  }
}

async function render(){
  const leads = await fetchLeads();
  const onlyUncontacted = document.getElementById('filter-uncontacted').checked;
  clearTable();
  const tbody = document.querySelector('#leads-table tbody');
  leads.filter(l=> !onlyUncontacted || !l.contacted).forEach(lead=>{
    const tr = document.createElement('tr');
    const score = lead.score || 0;
    const scoreClass = score >= 8 ? 'score-good' : (score >=5 ? 'score-okay' : 'score-bad');
    const scoreHtml = `<span class="score-badge ${scoreClass}">${score}</span>`;
    // prepare escaped preview text and tooltip with full content
    const expRaw = lead.explanation || lead.reason || '';
    const expEsc = escapeHtml(String(expRaw));
    const displayExp = expEsc.length > 300 ? expEsc.slice(0,300) + '…' : expEsc;
    const sugRaw = lead.suggestion || '';
    const sugEsc = escapeHtml(String(sugRaw));
    const displaySug = sugEsc.length > 140 ? sugEsc.slice(0,140) + '…' : sugEsc;
    tr.innerHTML = `<td>${lead.id}</td><td>${lead.business_name||''}</td><td><a href="${lead.website_url}" target="_blank">${lead.website_url}</a></td><td>${scoreHtml}</td><td title="${expEsc}">${displayExp}</td><td title="${sugEsc}">${displaySug}</td><td>${lead.contacted? 'Yes':'No'}</td><td><button class="btn-email" data-id="${lead.id}">Email</button> <button class="btn-rescan" data-id="${lead.id}">Delete & Rescan</button> <button class="btn-delete" data-id="${lead.id}">Delete</button></td><td>${formatDate(lead.created_at)}</td>`;
    tr.addEventListener('click', ()=> showDetail(lead));
    tbody.appendChild(tr);
    // attach run button handler
    // attach delete button handler
    // attach email preview handler
    const emailBtn = tr.querySelector('.btn-email');
    if(emailBtn){
      emailBtn.addEventListener('click', async (ev)=>{
        ev.stopPropagation();
        const id = emailBtn.getAttribute('data-id');
        showEmailModal(id);
      });
    }
    const delBtn = tr.querySelector('.btn-delete');
    if(delBtn){
      delBtn.addEventListener('click', async (ev)=>{
        ev.stopPropagation();
        const id = delBtn.getAttribute('data-id');
        if(!confirm('Delete lead ' + id + ' and remove screenshots/audits? This cannot be undone.')) return;
        delBtn.disabled = true;
        try{
          const resp = await fetch('/leads/' + id, {method: 'DELETE'});
          if(!resp.ok){
            const txt = await resp.text();
            throw new Error(txt || 'delete failed');
          }
          // remove row from table
          tr.remove();
          // if detail pane showing this lead, collapse it
          if(currentDetailId && currentDetailId == id){
            const detailEl = document.getElementById('detail');
            detailEl.style.display = 'none';
            currentDetailId = null;
          }
        }catch(e){
          console.error('Delete failed', e);
          alert('Delete failed: ' + (e.message||e));
          delBtn.disabled = false;
        }
      });
    }
    // Delete & Rescan handler
    const rescanBtn = tr.querySelector('.btn-rescan');
    if(rescanBtn){
      rescanBtn.addEventListener('click', async (ev)=>{
        ev.stopPropagation();
        const id = rescanBtn.getAttribute('data-id');
        if(!confirm('Delete lead ' + id + ' and immediately re-scan the URL?')) return;
        rescanBtn.disabled = true;
        try{
          // capture url and business name before deleting
          const url = lead.website_url;
          const name = lead.business_name || null;
          // delete existing lead
          const dresp = await fetch('/leads/' + id, {method: 'DELETE'});
          if(!dresp.ok){
            const txt = await dresp.text();
            throw new Error(txt || 'delete failed');
          }
          // trigger re-scan
          const sresp = await fetch('/scan-url', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({website_url: url, business_name: name})});
          if(!sresp.ok){
            const txt = await sresp.text();
            throw new Error(txt || 'scan failed');
          }
          const newLead = await sresp.json();
          // refresh list and show new lead detail
          await render();
          showDetail(newLead);
        }catch(e){
          console.error('Delete & Rescan failed', e);
          alert('Delete & Rescan failed: ' + (e.message||e));
          rescanBtn.disabled = false;
        }
      });
    }
  });
}

function escapeHtml(unsafe) {
  return unsafe
       .replace(/&/g, "&amp;")
       .replace(/</g, "&lt;")
       .replace(/>/g, "&gt;")
       .replace(/"/g, "&quot;")
       .replace(/'/g, "&#039;");
}

async function showEmailModal(leadId){
  const modal = document.getElementById('email-modal');
  const content = document.getElementById('modal-content');
  modal.style.display = 'flex';
  // ensure close handlers are attached (attach here to guarantee existence)
  const closeBtn = document.getElementById('modal-close');
  if(closeBtn) closeBtn.onclick = ()=>{ modal.style.display = 'none'; };
  const overlayEl = document.querySelector('#email-modal .modal-overlay');
  if(overlayEl) overlayEl.onclick = ()=>{ modal.style.display = 'none'; };
  content.innerHTML = 'Loading email preview...';
  try{
    const res = await fetch('/leads/' + leadId + '/generate-email', {method:'POST'});
    if(!res.ok){
      const txt = await res.text(); throw new Error(txt||'generate failed');
    }
    const j = await res.json();
    const subject = escapeHtml(j.subject||'');
    const preview = escapeHtml(j.preview||'');
    const body = escapeHtml(j.body||'');
    content.innerHTML = `
      <div><strong>Subject:</strong> ${subject}</div>
      <div style="margin-top:6px"><strong>Preview:</strong> ${preview}</div>
      <hr/>
      <div><strong>Body:</strong><pre style="white-space:pre-wrap;margin:8px 0">${body}</pre></div>
      <div style="display:flex;gap:8px;margin-top:8px"><button id="modal-copy-body" class="btn-audit">Copy Body</button><button id="modal-copy-subject" class="btn-audit">Copy Subject</button></div>
    `;
    const cb = document.getElementById('modal-copy-body');
    if(cb) cb.addEventListener('click', async ()=>{ try{ await navigator.clipboard.writeText(j.body); cb.textContent='Copied'; setTimeout(()=>cb.textContent='Copy Body',1500);}catch(e){alert('Copy failed: '+e.message)} });
    const cs = document.getElementById('modal-copy-subject');
    if(cs) cs.addEventListener('click', async ()=>{ try{ await navigator.clipboard.writeText(j.subject); cs.textContent='Copied'; setTimeout(()=>cs.textContent='Copy Subject',1500);}catch(e){alert('Copy failed: '+e.message)} });
  }catch(e){
    content.textContent = 'Failed to generate preview: ' + (e.message||e);
  }
}

// modal close wiring
const modalEl = document.getElementById('email-modal');
if(modalEl){
  const closeBtn = document.getElementById('modal-close');
  if(closeBtn) closeBtn.addEventListener('click', ()=>{ modalEl.style.display='none'; });
  const overlay = modalEl.querySelector('.modal-overlay');
  if(overlay) overlay.addEventListener('click', ()=>{ modalEl.style.display='none'; });
}

async function generateEmailPreview(leadId){
  const previewEl = document.getElementById('email-preview');
  previewEl.style.display = 'block';
  previewEl.textContent = 'Generating preview...';
  try{
    const res = await fetch('/leads/' + leadId + '/generate-email', {method:'POST'});
    if(!res.ok){
      const txt = await res.text();
      throw new Error(txt || 'generate failed');
    }
    const j = await res.json();
    previewEl.innerHTML = `<div><strong>Subject:</strong> ${j.subject}</div><div style="margin-top:6px"><strong>Preview:</strong> ${j.preview}</div><hr/><div><strong>Body:</strong><pre style="white-space:pre-wrap;margin:8px 0">${j.body}</pre></div><div><button id="copy-email" class="btn-audit">Copy Body</button></div>`;
    const cb = document.getElementById('copy-email');
    if(cb){
      cb.addEventListener('click', async ()=>{
        try{
          await navigator.clipboard.writeText(j.body);
          cb.textContent = 'Copied';
          setTimeout(()=>cb.textContent='Copy Body',1500);
        }catch(e){
          alert('Copy failed: ' + e.message);
        }
      });
    }
  }catch(e){
    previewEl.textContent = 'Failed to generate preview: ' + (e.message||e);
  }
}

async function fetchAuditSummary(leadId){
  const cell = document.getElementById('audit-summ-' + leadId);
  if(!cell) return;
  try{
    const res = await fetch('/leads/' + leadId + '/audit/latest');
    if(!res.ok) return; // nothing yet
    const j = await res.json();
    if(j.error){
      cell.innerHTML = `<span class="pill">error</span>`;
      return;
    }
    if(j.categories){
      const parts = [];
      const keys = {performance: 'P', accessibility: 'A', seo: 'S'};
      for(const k of Object.keys(keys)){
        const v = j.categories[k] && j.categories[k].score ? Math.round(j.categories[k].score*100) : null;
        if(v !== null) parts.push(`<span class="badge">${keys[k]}:${v}</span>`);
      }
      if(parts.length){
        cell.innerHTML = `<span class="audit-scores">${parts.join('')}</span>`;
      }
    }
  }catch(e){
    // ignore
  }
}

document.getElementById('refresh').addEventListener('click', render);
document.getElementById('filter-uncontacted').addEventListener('change', render);
const delUnBtn = document.getElementById('delete-uncontacted');
if(delUnBtn){
  delUnBtn.addEventListener('click', async ()=>{
    if(!confirm('Delete ALL uncontacted leads and their screenshots/audits? This cannot be undone.')) return;
    delUnBtn.disabled = true;
    try{
      const resp = await fetch('/leads/uncontacted', {method: 'DELETE'});
      if(!resp.ok){
        const txt = await resp.text();
        throw new Error(txt || 'delete failed');
      }
      const j = await resp.json();
      alert('Deleted ' + (j.count||0) + ' uncontacted leads');
      await render();
    }catch(e){
      console.error('Bulk delete failed', e);
      alert('Bulk delete failed: ' + (e.message||e));
    }finally{
      delUnBtn.disabled = false;
    }
  });
}

// Scan form handler
const scanForm = document.getElementById('scan-form');
if(scanForm){
  scanForm.addEventListener('submit', async (ev)=>{
    ev.preventDefault();
    const url = document.getElementById('scan-url').value.trim();
    const name = document.getElementById('scan-name').value.trim();
    const btn = document.getElementById('scan-btn');
    const status = document.getElementById('scan-status');
    if(!url) return;
    btn.disabled = true; status.textContent = 'Scanning...';
    try{
      const resp = await fetch('/scan-url', {method:'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({website_url: url, business_name: name||null})});
      if(!resp.ok){
        const text = await resp.text();
        throw new Error(text || 'Scan failed');
      }
      const lead = await resp.json();
      status.textContent = 'Done';
      // refresh list and show detail for created lead
      await render();
      showDetail(lead);
      // clear inputs
      document.getElementById('scan-url').value = '';
      document.getElementById('scan-name').value = '';
    }catch(e){
      console.error('Scan error', e);
      status.textContent = 'Error';
      alert('Scan failed: ' + (e.message||e));
    }finally{
      btn.disabled = false;
      setTimeout(()=>{ if(status.textContent==='Done') status.textContent=''; }, 3000);
    }
  });
}

// initial
render();
