const settings = {
  tokenKey: 'stillday.token',
  userKey: 'stillday.user',
  familyKey: 'stillday.family',
  uiStateKey: 'stillday.ui-state',
};
function readUiSnapshot() {
  try { return JSON.parse(sessionStorage.getItem(settings.uiStateKey) || 'null'); }
  catch { return null; }
}
const savedUiSnapshot = readUiSnapshot();
const restoreUiSnapshot = savedUiSnapshot?.user === (localStorage.getItem(settings.userKey) || 'local-owner')
  && savedUiSnapshot?.family === (localStorage.getItem(settings.familyKey) || '') ? savedUiSnapshot : null;
const state = {
  token: sessionStorage.getItem(settings.tokenKey) || 'local-development-only',
  user: localStorage.getItem(settings.userKey) || 'local-owner',
  family: localStorage.getItem(settings.familyKey) || '', savedAlbums: [],
  photos: [], view: ['library','memories','suggestions','album','compare','trash'].includes(restoreUiSnapshot?.view) ? restoreUiSnapshot.view : 'library', trash: false, grid: restoreUiSnapshot?.grid !== false, query: restoreUiSnapshot?.query || '', sort: restoreUiSnapshot?.sort || 'newest',
  filterPerson: restoreUiSnapshot?.filterPerson || '', filterOrientation: restoreUiSnapshot?.filterOrientation || '', selectedPair: new Set(), pairs: [], albumPhotos: null,
  chatMessages: Array.isArray(restoreUiSnapshot?.chatMessages) ? restoreUiSnapshot.chatMessages.slice(-40) : [], chatDraft: restoreUiSnapshot?.chatDraft || '', pendingRefreshRender: false,
  objectUrls: new Map(), pendingImages: new Map(), pendingUploads: new Set(), uploadStages: new Map(), timer: null,
  mode: 'local', photoCacheStatus: null,
  chatToolsAvailable: false, signedImages: new Map(), refreshing: false,
};
function persistUiState() {
  try {
    sessionStorage.setItem(settings.uiStateKey, JSON.stringify({
      user: state.user, family: state.family, view: state.view, grid: state.grid,
      query: state.query, sort: state.sort, filterPerson: state.filterPerson, filterOrientation: state.filterOrientation,
      chatMessages: state.chatMessages.slice(-40), chatDraft: state.chatDraft,
      albumPhotos: state.albumPhotos ? {title:state.albumPhotos.title, albumId:state.albumPhotos.albumId || null,
        photoIds:state.albumPhotos.photos.map(photo=>photo.photoId)} : null,
    }));
  } catch { /* A full session store should not block normal navigation. */ }
}
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
function updatePhotoCacheStatus() {
  const indicator = $('#photo-cache-status');
  if (!indicator) return;
  const status = state.photoCacheStatus || 'waiting';
  indicator.textContent = `API cache: ${status}`;
  indicator.dataset.cacheStatus = status.toLowerCase();
}
const photoPath = (photo) => `/a3/families/${encodeURIComponent(state.family)}/photos/${encodeURIComponent(photo.photoId)}`;
const tokenFor = (user) => user === state.user ? state.token : 'local-development-only';

function localUserHeaders(user = state.user) {
  return { Authorization: `Bearer ${tokenFor(user)}`, 'X-Dev-User': user };
}

function requestFailure(status, reason = '') {
  const authFailure = status === 401 || /invalid or expired access token|wrong token|missing bearer/i.test(reason);
  let advice;
  if (authFailure) advice = 'Your sign-in session is missing or expired. Open your account connection and sign in again, then retry.';
  else if (status === 403) advice = 'Access was denied. Check that you selected the correct family and that your account has permission for this action.';
  else if (status === 404) advice = 'This item is unavailable. Refresh the library; it may have been removed or may still be processing.';
  else if (status === 409) advice = 'This item changed before the update completed. Refresh it before trying again.';
  else if (status === 413) advice = 'The upload is too large. Choose an image no larger than 20 MB.';
  else if (status === 429) advice = 'Too many requests were sent. Wait a moment before trying again.';
  else if (status >= 500) advice = 'The service could not complete this request. A required service may be unavailable or not configured. Refresh to check the result before retrying; contact the project owner if this continues.';
  else advice = 'Check the information you entered and try again.';
  const detail = typeof reason === 'string' && reason.trim() ? ` Reason: ${reason.trim().slice(0, 450)}` : '';
  const error = new Error(`${advice}${detail} (HTTP ${status})`);
  error.status = status;
  return error;
}

async function request(url, options = {}, timeoutMs = 45000, readResponse = null) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener('abort', abort, {once:true});
  const timer = setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(url, {...options, signal:controller.signal});
    return readResponse ? await readResponse(response) : response;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out or was cancelled. Its result is not yet confirmed. Refresh the library before retrying to avoid submitting the same action twice.');
    if (error instanceof TypeError) throw new Error('The browser could not reach the service. Check your internet connection. The service or the upload connection may be unavailable. Refresh to check whether the action completed before retrying.');
    throw error;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

async function api(path, options = {}) {
  const isPhotoListRequest = (options.method || 'GET').toUpperCase() === 'GET'
    && /^\/a3\/families\/[^/]+\/photos(?:\?.*)?$/.test(path);
  const headers = new Headers(options.headers || {});
  if (options.auth !== false) {
    headers.set('Authorization', `Bearer ${options.token || state.token}`);
    headers.set('X-Dev-User', options.user || state.user);
  }
  let body = options.body;
  if (body !== undefined && !(body instanceof Blob) && !(body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  return request(path, {method:options.method || 'GET', headers, body, signal:options.signal}, 45000, async (response) => {
    if (isPhotoListRequest) {
      state.photoCacheStatus = (response.headers.get('x-cache') || 'unavailable').toUpperCase();
      updatePhotoCacheStatus();
    }
    const type = response.headers.get('content-type') || '';
    let value;
    try { value = type.includes('application/json') ? await response.json() : await response.blob(); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error(`The service returned an unreadable response (HTTP ${response.status}). Refresh to check the result before retrying.`);
    }
    if (!response.ok) throw requestFailure(response.status, value?.error || value?.detail);
    return value;
  });
}

async function pollSearchTask(task, progress) {
  if (!task?.jobId) return task;
  const family = state.family;
  const deadline = Date.now() + 620000;
  while (Date.now() < deadline) {
    progress?.(task.message || 'Processing your search');
    if (task.status === 'COMPLETED') return task.result;
    if (task.status === 'FAILED') throw new Error(task.error || 'The search could not be completed.');
    await new Promise(resolve => setTimeout(resolve, 2000));
    try {
      task = await api(`/a3/families/${encodeURIComponent(family)}/search-jobs/${encodeURIComponent(task.jobId)}`);
    } catch (error) {
      if (error.status && error.status < 500 && error.status !== 429) throw error;
      progress?.('Reconnecting to your search');
    }
  }
  throw new Error('The search exceeded its time limit. Please submit a new search.');
}

function notify(message, type = 'success') {
  const level = type || 'success';
  const title = {error:'Action failed', success:'Action completed', info:'Please note', progress:'In progress'}[level] || 'Update';
  const toast = document.createElement('div');
  toast.className = `toast ${level}`;
  toast.setAttribute('role', level === 'error' ? 'alert' : 'status');
  toast.innerHTML = `<div class="notice-copy"><strong class="notice-title">${title}</strong><p class="notice-message"></p></div><button type="button" class="notice-dismiss" aria-label="Dismiss notification">&times;</button>`;
  $('.notice-message', toast).textContent = message;
  $('.notice-dismiss', toast).onclick = () => toast.remove();
  $('#toast-region').append(toast);
  // Native modal dialogs are above all z-index layers: also show failures inside the dialog.
  const dialog = $('dialog[open]');
  if (dialog && level === 'error') {
    dialog.querySelector('.inline-notice')?.remove();
    const inline = toast.cloneNode(true);
    inline.classList.add('inline-notice');
    $('.notice-dismiss', inline).onclick = () => { inline.remove(); toast.remove(); };
    dialog.append(inline);
    inline.scrollIntoView({block:'nearest'});
  }
  if (level !== 'error' && level !== 'progress') window.setTimeout(() => toast.remove(), 10000);
  return {
    update(text) { $('.notice-message', toast).textContent = text; },
    close() { toast.remove(); },
  };
}

function processingExplanation(photo) {
  const uploadStage = state.uploadStages.get(photo.photoId);
  if (uploadStage) return uploadStage;
  if (photo.analysisError) return `AI caption or vector indexing failed (${photo.analysisError}). Check model permissions and retry analysis.`;
  if (photo.status === 'QUEUED') return 'Uploaded and queued. Background processing has not started yet; starting a worker can take a few minutes.';
  if (photo.status === 'PROCESSING') return 'Processing image sizes. This page checks for completion automatically.';
  if (photo.status === 'UPLOADING') return 'Upload completion has not been confirmed. Refresh before submitting this photo again.';
  if (photo.status === 'QUEUE_FAILED') return 'The original was saved, but the processing job could not be queued. Refresh to retry or contact the project owner.';
  if (photo.status !== 'FAILED') return '';
  const reasons = {
    UnidentifiedImageError:'The file could not be decoded as an image. Try a valid JPEG, PNG or WebP file.',
    DecompressionBombError:'The image dimensions are too large. Resize the image before uploading again.',
    ClientError:'A cloud service rejected a processing request. The stored status does not identify the exact cause; the project owner needs to check processing logs.',
    ValueError:'The image or processing input failed validation. Check the file format and dimensions; contact the project owner if it continues.',
  };
  return 'Processing failed. ' + (reasons[photo.error] || 'The background processor did not record a detailed user-facing cause. Refresh to see whether a retry succeeds; contact the project owner if it remains failed.');
}

function startPhotoStatusPolling() {
  if (!state.timer) state.timer=window.setInterval(()=>refreshLibrary({quiet:true}),5000);
}

function setBusy(element, busy, busyText = 'Working…') {
  if (!element) return;
  element.disabled = busy;
  element.setAttribute('aria-busy', String(busy));
  if (busy) {
    if (!element.dataset.label) element.dataset.label = element.innerHTML;
    element.innerHTML = `<span class="mini-spinner"></span>${escapeHtml(busyText)}`;
  } else if (element.dataset.label) {
    element.innerHTML = element.dataset.label;
    delete element.dataset.label;
  }
}

function uploadDate(value) {
  if(!value)return 'Not recorded';
  const date=new Date(value);
  return Number.isNaN(date.valueOf())?'Not recorded':new Intl.DateTimeFormat('en-AU',{dateStyle:'medium',timeStyle:'short'}).format(date);
}

function friendlyDate(value) {
  if (!value) return 'Date not added';
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat('en-AU', { day:'numeric', month:'short', year:'numeric' }).format(date);
}

function todayKey() {
  const value = new Date();
  return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
}

function photoById(id) { return state.albumPhotos?.photos?.find((photo) => photo.photoId === id) || state.photos.find((photo) => photo.photoId === id); }

async function photosByIds(ids, resultPhotos=[]) {
  const unique = [...new Set(ids || [])];
  const results = new Map(resultPhotos.map((photo)=>[photo.photoId,photo]));
  const photos = await Promise.all(unique.map(async (id) => {
    const cached = photoById(id);
    const result = results.get(id);
    if(result)return {...(cached||{}),...result};
    if (cached) return cached;
    try { return await api(photoPath({photoId:id})); } catch { return null; }
  }));
  return photos.filter((photo) => photo && photo.status === 'READY');
}

function currentPhotos() {
  const values = state.photos.filter((photo) => state.trash ? photo.status === 'TRASHED' : photo.status !== 'TRASHED');
  const activeUploads = values.filter((photo) => state.pendingUploads.has(photo.photoId)
    && !['READY','FAILED','TRASHED'].includes(photo.status));
  let result = values;
  if (state.filterPerson) result = result.filter((photo) => (photo.people || []).includes(state.filterPerson));
  if (state.filterOrientation) result = result.filter((photo) => (photo.orientation || '').toLocaleLowerCase() === state.filterOrientation);
  if (state.query.trim()) {
    const term = state.query.toLocaleLowerCase();
    result = result.filter((photo) => [photo.name, photo.caption, photo.capturedDate, ...(photo.people || [])].join(' ').toLocaleLowerCase().includes(term));
  }
  const activeIds = new Set(activeUploads.map((photo) => photo.photoId));
  result = [...activeUploads, ...result.filter((photo) => !activeIds.has(photo.photoId))];
  return result.sort((a, b) => {
    const activeA = state.pendingUploads.has(a.photoId) && !['READY','FAILED','TRASHED'].includes(a.status);
    const activeB = state.pendingUploads.has(b.photoId) && !['READY','FAILED','TRASHED'].includes(b.status);
    if (activeA !== activeB) return activeA ? -1 : 1;
    if (state.sort === 'oldest') return (a.capturedDate || a.uploadedAt || '').localeCompare(b.capturedDate || b.uploadedAt || '');
    if (state.sort === 'sharpest') return (b.sharpness || 0) - (a.sharpness || 0);
    return (b.capturedDate || b.uploadedAt || '').localeCompare(a.capturedDate || a.uploadedAt || '');
  });
}

function updateSidebar() {
  const select = $('#family-select');
  if (state.family && ![...select.options].some((option) => option.value === state.family)) {
    const option = document.createElement('option');
    option.value = state.family;
    option.textContent = localStorage.getItem(`stillday.family-name.${state.family}`) || 'My family album';
    select.add(option);
  }
  select.value = state.family;
  const name = state.user === 'local-owner' ? 'Local owner' : state.user.replaceAll('-', ' ');
  $('#profile-name').textContent = name;
  $('#profile-avatar').textContent = (name[0] || 'L').toUpperCase();
  $('#mobile-profile').textContent = (name[0] || 'L').toUpperCase();
  const signedIn = state.mode === 'aws' && state.token !== 'local-development-only';
  $('#profile-role').textContent = signedIn ? 'Signed in' : state.photos.length ? 'Family member' : state.user === 'local-owner' ? 'Family admin' : 'Demo account';
  $('#identity-toggle').hidden = state.mode === 'aws';
  const topAccount=$('#mobile-profile');
  if(state.mode==='aws'){topAccount.classList.remove('avatar');topAccount.classList.add('account-state-button');topAccount.textContent=signedIn?'Sign out':'Sign in';topAccount.title=signedIn?'Sign out of Stillday':'Sign in to Stillday';topAccount.setAttribute('aria-label',topAccount.title);}
  else{topAccount.classList.remove('account-state-button');topAccount.classList.add('avatar');topAccount.textContent=(name[0]||'L').toUpperCase();topAccount.title='Current profile';topAccount.setAttribute('aria-label','Current profile');}
  $('#photo-count').textContent = String(state.photos.filter((photo) => photo.status !== 'TRASHED').length);
  const ready = state.photos.filter((photo) => photo.status === 'READY');
  const exactCopies = new Set(ready.map((photo) => photo.sha256).filter((hash) => ready.filter((photo) => photo.sha256 === hash).length > 1));
  const review = ready.filter((photo) => photo.qualityWarnings?.length).length + exactCopies.size;
  $('#review-count').textContent = String(Math.min(review, 99));
  $$('.nav-link').forEach((button) => button.classList.toggle('active', button.dataset.view === state.view));
  const labels = {library:'All photos', memories:'On this day', suggestions:'Needs a look', compare:'Photo pairs', trash:'Recently deleted'};
  $('#breadcrumbs').innerHTML = `Your space <span>/</span> ${escapeHtml(labels[state.view] || 'All photos')}`;
}

function imageUrl(photo, size = '320') {
  if (!photo || !['READY','TRASHED'].includes(photo.status) || !state.family) return Promise.resolve('');
  const key = `${photo.photoId}:${size}`;
  if (state.objectUrls.has(key)) return Promise.resolve(state.objectUrls.get(key));
  if (state.pendingImages.has(key)) return state.pendingImages.get(key);
  const cached=state.signedImages.get(key);
  if(cached&&cached.expires>Date.now())return Promise.resolve(cached.url);
  const requestedSize=photo.status==='TRASHED'&&!photo.variants?.length?'original':size;
  const request = api(`${photoPath(photo)}/download?size=${encodeURIComponent(requestedSize)}`).then(async (result) => {
    if (!(result instanceof Blob) && result?.url && new URL(result.url, location.href).origin !== location.origin) {
      state.signedImages.set(key,{url:result.url,expires:Date.now()+Math.max(1,(result.expiresIn||300)-30)*1000});
      state.pendingImages.delete(key);
      return result.url;
    }
    const blob = result instanceof Blob ? result : await fetch(result.url).then((response) => {
      if (!response.ok) throw new Error('This image could not be opened. Refresh your access and try again.');
      return response.blob();
    });
    const url = URL.createObjectURL(blob);
    state.objectUrls.set(key, url);
    state.pendingImages.delete(key);
    return url;
  }).catch((error) => {
    state.pendingImages.delete(key);
    throw error;
  });
  state.pendingImages.set(key, request);
  return request;
}

function originalPreviewUrl(photo) {
  const key=`${photo.photoId}:preview`;
  if(state.objectUrls.has(key))return Promise.resolve(state.objectUrls.get(key));
  if(state.pendingImages.has(key))return state.pendingImages.get(key);
  const request=api(`${photoPath(photo)}/download?size=original`).then(async(result)=>{
    const blob=result instanceof Blob?result:await fetch(result.url).then((response)=>{
      if(!response.ok)throw new Error('Original photo preview is temporarily unavailable.');
      return response.blob();
    });
    const url=URL.createObjectURL(blob);state.objectUrls.set(key,url);state.pendingImages.delete(key);return url;
  }).catch((error)=>{state.pendingImages.delete(key);throw error;});
  state.pendingImages.set(key,request);return request;
}

function forgetImages(photoId) {
  for(const key of state.signedImages.keys())if(key.startsWith(`${photoId}:`))state.signedImages.delete(key);
  for (const [key, url] of state.objectUrls) {
    if (key.startsWith(`${photoId}:`)) { URL.revokeObjectURL(url); state.objectUrls.delete(key); }
  }
}

function hydrateImages(root = document) {
  state.photoObserver?.disconnect();
  const images=$$('img[data-photo-id],img[data-pending-photo-id]',root);
  if (!('IntersectionObserver' in window)) {
    images.forEach((image) => loadImage(image));
    return;
  }
  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (entry.isIntersecting) { observer.unobserve(entry.target); loadImage(entry.target); }
  }), { rootMargin: '240px 0px' });
  state.photoObserver=observer;
  images.forEach((image) => observer.observe(image));
}

function loadImage(image) {
  const photo = photoById(image.dataset.photoId||image.dataset.pendingPhotoId);
  if (!photo) return;
  const request=['READY','TRASHED'].includes(photo.status)?imageUrl(photo,image.dataset.size||'320'):
    ['QUEUED','PROCESSING'].includes(photo.status)?originalPreviewUrl(photo):Promise.resolve('');
  request.then((url) => {
    if (url && image.isConnected) {
      image.src = url;
      image.classList.remove('skeleton');
      image.alt = `${photo.name || 'Family photo'}${photo.people?.length ? ` featuring ${photo.people.join(', ')}` : ''}`;
      image.parentElement?.querySelector('.photo-placeholder')?.remove();
    }
  }).catch((error) => {
    const placeholder = image.parentElement?.querySelector('.photo-placeholder');
    if (placeholder) { placeholder.textContent = 'Image unavailable — open Details or refresh to retry.'; placeholder.title = error.message; }
  });
}

function emptyState(title, description, buttonText = '', action = '') {
  return `<div class="empty-state"><div class="empty-illustration" aria-hidden="true">✧</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p>${buttonText ? `<button class="button button-primary" data-action="${escapeHtml(action)}">${escapeHtml(buttonText)} <span>→</span></button>` : ''}</div>`;
}

function card(photo) {
  const status = photo.status !== 'READY' ? `<span class="photo-status">${escapeHtml(photo.status.toLowerCase().replaceAll('_',' '))}</span>` : '';
  const pendingPreview = photo.status !== 'READY' && state.objectUrls.has(`${photo.photoId}:preview`)
    ? `<img class="pending-preview" src="${escapeHtml(state.objectUrls.get(`${photo.photoId}:preview`))}" alt="Preview of ${escapeHtml(photo.name || 'uploading photo')}">`
    : ['QUEUED','PROCESSING'].includes(photo.status)
      ? `<img class="pending-preview" data-pending-photo-id="${escapeHtml(photo.photoId)}" alt="Preview of ${escapeHtml(photo.name || 'uploading photo')}" loading="lazy">`
      : `<div class="pending-art" aria-hidden="true">${photo.status === 'FAILED' ? '!' : '◷'}</div>`;
  const selected = state.selectedPair.has(photo.photoId);
  const action = state.view === 'trash' || photo.status === 'TRASHED' ? `<button class="card-action restore-action" type="button" data-action="restore" data-id="${escapeHtml(photo.photoId)}" aria-label="Restore ${escapeHtml(photo.name || 'photo')}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8m0-5v5h5m4-3v7l4 2"/></svg><span>Restore photo</span></button>` : `<button class="card-action" data-action="edit" data-id="${escapeHtml(photo.photoId)}">Details</button>`;
  const savedAlbum = Boolean(state.albumPhotos?.albumId);
  const deleteControl = state.view !== 'trash' && photo.status !== 'TRASHED' ? `<button class="photo-trash ${savedAlbum?'album-photo-remove':''}" type="button" data-action="${savedAlbum?'remove-album-photo':'trash'}" data-id="${escapeHtml(photo.photoId)}" title="${savedAlbum?'Remove from this album':'Move to recently deleted'}" aria-label="${savedAlbum?'Remove':'Move'} ${escapeHtml(photo.name || 'photo')} ${savedAlbum?'from this album':'to recently deleted'}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-.8 13H6.8L6 7m4 3v7m4-7v7"/></svg></button>` : '';
  return `<article class="photo-card" data-photo-id="${escapeHtml(photo.photoId)}" tabindex="0" aria-label="${escapeHtml(photo.name || 'Family photo')}, ${escapeHtml(photo.status)}">
    <div class="photo-frame" data-action="${state.view==='compare'?'pair-toggle':'preview'}" data-id="${escapeHtml(photo.photoId)}">
      ${['READY','TRASHED'].includes(photo.status) ? `<div class="photo-placeholder" aria-hidden="true">✧</div><img class="skeleton" data-photo-id="${escapeHtml(photo.photoId)}" data-size="320" alt="" loading="lazy">` : pendingPreview}
      ${status}${photo.favorite ? '<span class="photo-favorite" title="Favourite">♥</span>' : ''}${deleteControl}
      ${state.view === 'compare' && photo.status === 'READY' ? `<button type="button" class="pair-selector" data-action="pair-toggle" data-id="${escapeHtml(photo.photoId)}" aria-pressed="${selected}">${selected?'Selected '+([...state.selectedPair].indexOf(photo.photoId)+1):'Select photo'}</button>` : ''}
    </div>
    <div class="photo-info"><div class="photo-title">${escapeHtml(photo.name || 'Untitled photo')}</div><div class="photo-meta"><span>Taken: ${escapeHtml(friendlyDate(photo.capturedDate))}</span>${photo.orientation ? `<span class="meta-dot"></span><span>${escapeHtml(photo.orientation)}</span>` : ''}</div>
      <p class="photo-uploaded">Uploaded: ${escapeHtml(uploadDate(photo.uploadedAt))}</p>
      ${photo.caption ? `<p class="caption-snippet">${escapeHtml(photo.caption)}</p>` : ''}${(photo.people?.length||photo.labels?.length) ? `<div class="tag-row">${[...(photo.people||[]),...(photo.labels||[])].slice(0,5).map((tag)=>`<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>` : ''}${photo.analysisError || (photo.status !== 'READY' && photo.status !== 'TRASHED') ? `<p class="processing-feedback ${photo.analysisError || photo.status === 'FAILED' ? 'failed' : ''}">${escapeHtml(processingExplanation(photo))}</p>` : ''}${action}</div></article>`;
}

function grid(photos = currentPhotos()) {
  if (!photos.length) {
    if (!state.family) return emptyState('Start with your family', 'Create a private family album, then add the pictures you want to keep close.', 'Create a family album', 'new-family');
    if (state.view === 'trash') return emptyState('Nothing in the recycle bin', 'Photos you move here stay out of your albums until you restore them.');
    if (state.albumPhotos) return emptyState('No matching photos', 'No ready photos match this selection. Try another orientation or return to All photos.');
    return emptyState('Your story starts here', 'Add a few photos to build your library. Stillday sorts them in the background while you browse.', 'Add your first photo', 'upload');
  }
  return `<div class="photo-grid ${state.grid?'':'photo-list'}" id="photo-grid">${photos.map(card).join('')}</div>`;
}

function memoryBanner() {
  const today = todayKey();
  const memories = state.photos.filter((photo) => photo.status === 'READY' && !photo.hideMemory && photo.capturedDate && photo.capturedDate.slice(5) === today.slice(5) && photo.capturedDate.slice(0,4) < today.slice(0,4));
  return `<section class="feature-banner memory-banner"><div class="banner-copy"><span class="banner-label">✳ &nbsp; A LITTLE TRIP BACK</span><h2>${memories.length ? `${memories.length} familiar moment${memories.length===1?'':'s'} from this day` : 'A memory might be waiting'}</h2><p>${memories.length ? 'Take another look at the moments that found their way into this album.' : 'Add the dates your pictures were taken to see them return here, year after year.'}</p><button class="banner-action" data-action="view-memories">${memories.length?'Revisit these memories':'Explore this day'} &nbsp; →</button></div><div class="banner-photos" aria-hidden="true">${[0,1,2].map((index)=>`<div class="banner-photo">${memories[index]?`<img data-photo-id="${escapeHtml(memories[index].photoId)}" data-size="320" alt="" loading="lazy">`:''}</div>`).join('')}</div></section>`;
}

function orientationFilter() {
  const selected = (value) => state.filterOrientation === value ? ' selected' : '';
  return `<div class="toolbar photo-toolbar"><label class="filter-chip" for="orientation-filter">Orientation<select id="orientation-filter" aria-label="Filter photos by orientation"><option value="">All orientations</option><option value="landscape"${selected('landscape')}>Landscape</option><option value="portrait"${selected('portrait')}>Portrait</option><option value="square"${selected('square')}>Square</option></select></label></div>`;
}

function renderLibrary() {
  const existingComposer=$('#chat-form');
  const oldThread=$('#chat-thread');
  const threadTop=oldThread?.scrollTop||0;
  const stickToBottom=!oldThread||oldThread.scrollHeight-oldThread.scrollTop-oldThread.clientHeight<40;
  const photos = currentPhotos();
  const ready = state.photos.filter((photo) => photo.status === 'READY').length;
  const messages=state.chatMessages.length?state.chatMessages:[{role:'assistant',text:state.family?`Your family library is ready. ${ready} ${ready===1?'photo':'photos'} are ready to explore. Ask me to find a person, a date, or a moment.`:'Create a family library to get started. Then ask me to find photos, revisit memories, or tidy up duplicates.'}];
  const layout=document.createElement('template');
  layout.innerHTML = `<div class="chat-page"><header class="chat-heading"><div><span class="eyebrow">YOUR FAMILY PHOTO LIBRARY</span><h1>What would you like to do?</h1><p>Ask for a task and I will call the matching photo-library tool.</p></div><button class="button button-primary" data-action="upload-files">＋ Add photos</button></header>
    <section class="chat-panel" aria-label="Photo library assistant"><div class="chat-thread" id="chat-thread" aria-live="polite">${messages.map((message)=>`<div class="chat-message ${message.role==='user'?'user-message':message.role==='tool'?'tool-message':'assistant-message'}"><span class="chat-avatar">${message.role==='user'?'You':message.role==='tool'?'Tool':'S'}</span><div class="chat-bubble">${escapeHtml(message.text)}${message.clarification?.choices?.length?`<div class="clarification-choices">${message.clarification.choices.map(choice=>`<button type="button" data-action="resolve-clarification" data-prompt="${escapeHtml(choice.prompt)}">${escapeHtml(choice.label)}</button>`).join('')}</div>`:''}</div></div>`).join('')}</div>
    <form class="chat-composer" id="chat-form"><label class="visually-hidden" for="chat-input">Ask about your photos</label><textarea id="chat-input" name="prompt" rows="1" maxlength="2000" placeholder="Try “Show me photos of Mum from last summer”…" required></textarea><button class="chat-send" type="submit" aria-label="Send message">↑</button></form>
    <div class="chat-shortcuts"><button data-action="chat-today">On this day</button><button data-action="chat-review">Find duplicates and quality issues</button><button data-action="reanalyze-photos">Analyse existing photos for search</button><button data-action="chat-play">Play slideshow</button><button data-action="chat-family">Family access</button></div></section>
    <section class="chat-results"><div class="section-heading"><div><h2>${state.albumPhotos?escapeHtml(state.albumPhotos.title):'Your recent photos'}</h2><p>${state.albumPhotos?`${state.albumPhotos.photos.length} matching photos`: `${ready} ready · ${state.photos.filter((photo)=>['UPLOADING','QUEUED','PROCESSING'].includes(photo.status)).length} processing`}</p></div><span id="photo-cache-status" class="photo-cache-status" aria-live="polite"></span>${state.albumPhotos?`${state.albumPhotos.photos.length?`<button class="text-button" data-action="save-results-as-album" ${state.albumPhotos.albumId?'disabled':''}>${state.albumPhotos.albumId?'Saved in Albums':'Save as album'}</button>`:''}<button class="text-button" data-action="clear-chat-results">All photos</button>`:''}</div>${state.albumPhotos ? "" : orientationFilter()}${grid(state.albumPhotos?.photos||photos)}</section></div>`;
  if(existingComposer)layout.content.querySelector('#chat-form').replaceWith(existingComposer);
  $('#page-content').replaceChildren(layout.content);
  updatePhotoCacheStatus();
  const thread=$('#chat-thread');
  thread.scrollTop=stickToBottom?thread.scrollHeight:threadTop;
  hydrateImages($('#page-content'));
  persistUiState();
}

async function submitChat(prompt) {
  const text=prompt.trim();if(!text)return;
  if(/^(?:please\s+)?(?:play|start)(?:\s+the)?\s+slideshow[.!]?$/i.test(text)){playCurrentAlbum();return;}
  const lower=text.toLocaleLowerCase();const previousAlbum=state.albumPhotos;const previousResults=previousAlbum?.photos||[];state.view='library';state.albumPhotos=null;
  const hasPeopleTerm=/\b(people|person|persons|faces|family members|humans|man|woman|child|children|kids|group|couple)\b/.test(lower);
  const isPeopleRequest=hasPeopleTerm&&!/\b(explain|define|what does|what is)\b/.test(lower);
  const orientationRequest=/(?:\u6a2a\u5411|\u6a2a\u7248|\u6a2a\u5c4f|\u7eb5\u5411|\u7ad6\u5411|\u7ad6\u7248|\u7ad6\u5c4f|\u65b9\u5f62|\u6b63\u65b9\u5f62|(?<![a-z])(?:landscape|horizontal|portrait|vertical|square)(?![a-z]))/i.test(text);
  state.chatMessages.push({role:'user',text});renderLibrary();
  const say=(message)=>state.chatMessages.push({role:'assistant',text:message});
  const invoke=async(name,operation)=>{
    const labels={bedrock_converse_tool_agent:'Understanding your request',list_family_photos:'Loading your photos',find_on_this_day:'Looking for memories from this day',review_photo_library:'Checking for similar and low-quality photos',create_photo_album:'Building your photo album',create_filtered_album:'Filtering your photos',start_slideshow:'Preparing your slideshow',restore_photo:'Restoring your photo',restore_deleted_photos:'Restoring deleted photos',move_photo_to_trash:'Moving the photo to Recently deleted',update_photo_details:'Saving photo details',create_photo_comparison:'Creating your photo comparison'};
    const label=labels[name]||name.replaceAll('_',' ');
    const call={role:'tool',text:`Working: ${label}…`};state.chatMessages.push(call);renderLibrary();
    try{const initial=await operation();const result=await pollSearchTask(initial,message=>{call.text=message;renderLibrary();});call.text=`Done: ${label}.`;return result;}
    catch(error){call.text=`Could not ${label.toLocaleLowerCase()}: ${error.message}`;throw error;}
    finally{renderLibrary();}
  };
  const putResult=(title,photos)=>{state.albumPhotos={title,photos};};
  try{
    if(/^(hi|hello|hey|help|what can you do)[?.! ]*$/.test(lower)){
      say('I can find photos, make albums, show On This Day, review duplicates and quality, play slideshows, restore deleted photos, edit details, compare photos, upload pictures, and manage family access.');
    }else if(/upload|add photos|add pictures/.test(lower)){
      if(!state.family){say('Create a family library first.');renderLibrary();openDialog('family-dialog');return;}
      await invoke('open_photo_upload',async()=>$('#file-input').click());say('Choose photos in the file picker. Uploading and image processing happen in the background.');
    }else if(/create family|new family|family album/.test(lower)){say('Opening family setup.');renderLibrary();openDialog('family-dialog');return;}
    else if(/family access|invite|add member|share with family/.test(lower)&&state.family){say('Opening family access. Add an existing account by its Cognito user ID.');renderLibrary();openDialog('members-dialog');return;}
    else if(state.family && isPeopleRequest && !state.chatToolsAvailable && !/\b(album|collection|gather)\b/.test(lower)){
      const hits=await invoke('semantic_photo_search',()=>api(`/a3/families/${encodeURIComponent(state.family)}/search`,{method:'POST',body:{query:text,background:true,requestId:crypto.randomUUID()}}));
      if(hits?.clarification){state.chatMessages.push({role:'assistant',text:hits.reply,clarification:hits.clarification});renderLibrary();return;}
      const matches=(hits||[]).map(hit=>hit.photo).filter(Boolean);
      putResult('Photos with people',matches);
      say(matches.length?`Found ${matches.length} photos using AI semantic search.`:'No photos matched that description. Choose “Analyse existing photos for search” to refresh photo captions, people counts and semantic vectors, then try again.');
    }else if((state.chatToolsAvailable||orientationRequest)&&state.family){
      const response=await invoke('bedrock_converse_tool_agent',()=>api(`/a3/families/${encodeURIComponent(state.family)}/chat`,{method:'POST',body:{prompt:text,background:true,requestId:crypto.randomUUID()}}));
      if(response.clarification){state.chatMessages.push({role:'assistant',text:response.reply,clarification:response.clarification});renderLibrary();return;}
      let trashCancelled=false;
      for(const call of response.toolCalls||[]){
        state.chatMessages.push({role:'tool',text:`${call.name} - ${call.status}`});
        if(call.status!=='completed')continue;
        const value=call.result;
        if(['list_photos','search_photos','on_this_day','list_deleted_photos'].includes(call.name)){
          const values=Array.isArray(value)?value:[];
          const photos=await photosByIds(values.map(item=>item.photoId),values);
          const title=isPeopleRequest&&call.name==='search_photos'?'Photos with people':({list_photos:'Your photo library',search_photos:'Matching photos',on_this_day:'On this day',list_deleted_photos:'Recently deleted'})[call.name];
          putResult(title,photos);
        }else if(call.name==='review_suggestions'){
          const ids=[...(value.qualityReview||[]).map(item=>item.photoId),...(value.groups||[]).flatMap(group=>[group.referencePhotoId,...group.matches.map(item=>item.photoId)])];putResult('Photos to review',[...new Set(ids)].map(photoById).filter(Boolean));
        }else if(call.name==='create_album'){
          const photos=await photosByIds(value.photoIds||[]);putResult(value.title||'Your new album',photos);
        }else if(call.name==='start_slideshow'){
          const ids=previousResults.map(item=>item.photoId);
          const selectedIds=previousAlbum!==null?ids:(value||[]).map(item=>item.photoId);
          state.albumPhotos=previousAlbum;
          if(selectedIds.length)renderPlayer(selectedIds[0],selectedIds);
          else notify('This selection has no ready photos to play.','info');
        }else if(call.name==='request_trash_photo'){
          const photo=photoById(value?.photo?.photoId);
          if(photo&&value.requiresUserConfirmation&&await confirmTrash(photo)){
            await invoke('move_photo_to_trash',()=>api(`${photoPath(photo)}/trash`,{method:'POST',body:{}}));await refreshLibrary({quiet:true});
          }else trashCancelled=true;
        }else if(['restore_photo','update_photo_details'].includes(call.name)){await refreshLibrary({quiet:true});}
        else if(call.name==='create_photo_comparison'){await loadPairs();state.view='compare';}
      }
      say(trashCancelled?'Cancelled. The photo remains in your library.':response.reply||'The photo tools completed.');
    }
    else if(/compare|then.and.now|recreation|recreate/.test(lower)){say('Opening photo comparison. Select two photos to create a then-and-now pair.');await navigate('compare');return;}
    else if(/edit|details|tag|date taken|favorite|favourite/.test(lower)){putResult('Choose a photo and select Details to edit its people, capture date or favourite status.',state.photos.filter(p=>p.status==='READY'));say('Choose a photo below, then select Details. I will open the photo details editor.');}
    else if(!state.family){say('Create a family library first, then I can use the photo tools.');renderLibrary();openDialog('family-dialog');return;}
    else if(/all photos|show (?:me )?(?:all|recent) photos|recent photos|list photos|my library/.test(lower)){
      const result=await invoke('list_family_photos',()=>api(`/a3/families/${encodeURIComponent(state.family)}/photos?trash=true`));state.photos=Array.isArray(result)?result:[];state.albumPhotos=null;updateSidebar();say(`Loaded ${state.photos.filter(p=>p.status!=='TRASHED').length} photos from your family library.`);
    }else if(/today|on this day|history/.test(lower)){
      const photos=await invoke('find_on_this_day',()=>api(`/a3/families/${encodeURIComponent(state.family)}/memories/on-this-day?date=${todayKey()}`));putResult('On this day',photos);say(photos.length?`Found ${photos.length} photos from this day in earlier years.`:'No earlier photos have this date. Add capture dates and I can find them next time.');
    }else if(/duplicate|blurry|quality|review|tidy|clean up/.test(lower)){
      const result=await invoke('review_photo_library',()=>api(`/a3/families/${encodeURIComponent(state.family)}/suggestions`));
      const ids=[...(result.qualityReview||[]).map(item=>item.photoId),...(result.groups||[]).flatMap(group=>[group.referencePhotoId,...group.matches.map(item=>item.photoId)])];
      putResult('Photos to review', [...new Set(ids)].map(photoById).filter(Boolean));say(state.albumPhotos.photos.length?`Found ${state.albumPhotos.photos.length} photos to review. Nothing is deleted automatically.`:'No duplicate or quality suggestions right now.');
    }else if(/restore/.test(lower)){
      const deleted=state.photos.filter(p=>p.status==='TRASHED');
      const match=deleted.filter(p=>p.name&&lower.includes(p.name.toLocaleLowerCase()));
      if(/restore.*all|restore them all/.test(lower)){await invoke('restore_deleted_photos',async()=>{for(const item of deleted)await api(`${photoPath(item)}/restore`,{method:'POST',body:{}});await refreshLibrary({quiet:true});});say(`Restored ${deleted.length} photos.`);}
      else if(match.length===1){await invoke('restore_photo',()=>api(`${photoPath(match[0])}/restore`,{method:'POST',body:{}}));await refreshLibrary({quiet:true});say(`Restored ${match[0].name||'the photo'}.`);}
      else{putResult('Recently deleted',deleted);say('Choose a deleted photo below and select Restore, or say \"restore all deleted photos\".');}
    }else if(/deleted|recycle bin|trash/.test(lower)){
      const deleted=state.photos.filter(p=>p.status==='TRASHED');
      if(/restore.*all|restore them all/.test(lower)){
        await invoke('restore_deleted_photos',async()=>{for(const item of deleted)await api(`${photoPath(item)}/restore`,{method:'POST',body:{}});await refreshLibrary({quiet:true});});say(`Restored ${deleted.length} photos.`);
      }else{putResult('Recently deleted - say restore all to restore these photos',deleted);say(`${deleted.length} photos are in Recently deleted. They remain there until you restore them.`);}
    }else if(/play|slideshow/.test(lower)){
      const photos=previousResults.length?previousResults:state.photos.filter(p=>p.status==='READY');
      await invoke('start_slideshow',async()=>{if(!photos.length)throw new Error('There are no ready photos to play yet.');renderPlayer(photos[0].photoId,photos.map(photo=>photo.photoId));});say(`Playing ${photos.length} photos. Use the controls to pause or move through them.`);
    }else if(/delete|remove|move.*trash/.test(lower)){
      const match=state.photos.filter(p=>p.status==='READY'&&p.name&&lower.includes(p.name.toLocaleLowerCase()));
      if(match.length===1){
        if(await confirmTrash(match[0])){await invoke('move_photo_to_trash',()=>api(`${photoPath(match[0])}/trash`,{method:'POST',body:{}}));await refreshLibrary({quiet:true});say('Moved the photo to Recently deleted. You can restore it any time.');}
        else say('Cancelled. The photo is still in your library.');
      }else{putResult('Choose a photo to review',state.photos.filter(p=>p.status==='READY'));say('I have not moved anything. Select a photo below and use Details to review it.');}
    }else if(!state.chatToolsAvailable&&!/\b(album|collection|gather)\b/.test(lower)){
      const hits=await invoke('semantic_photo_search',()=>api(`/a3/families/${encodeURIComponent(state.family)}/search`,{method:'POST',body:{query:text,background:true,requestId:crypto.randomUUID()}}));
      const photos=(hits||[]).map(hit=>hit.photo).filter(Boolean);
      putResult('Matching photos',photos);
      say(photos.length?`Found ${photos.length} matching photos using semantic photo search.`:'No photos matched that description.');
    }else{
      let result,plannerNote='';
      try{result=await invoke('create_photo_album',()=>api(`/a3/families/${encodeURIComponent(state.family)}/albums`,{method:'POST',body:{title:text.slice(0,100)||'Photo search',filters:{limit:100},prompt:text}}));}
      catch(error){
        const people=[...new Set(state.photos.flatMap(p=>p.people||[]))];const person=people.find(name=>lower.includes(name.toLocaleLowerCase()));const year=lower.match(/\b(19|20)\d{2}\b/)?.[0];const filters={limit:100};if(person)filters.person=person;if(year){filters.since=`${year}-01-01`;filters.until=`${year}-12-31`;}
        if(person||year){plannerNote='The language model planner is unavailable, so I used saved person/date filters.';result=await invoke('create_filtered_album',()=>api(`/a3/families/${encodeURIComponent(state.family)}/albums`,{method:'POST',body:{title:text.slice(0,100)||'Photo search',filters}}));}
        else{const hits=await invoke('semantic_photo_search',()=>api(`/a3/families/${encodeURIComponent(state.family)}/search`,{method:'POST',body:{query:text,background:true,requestId:crypto.randomUUID()}}));const found=(hits||[]).map(hit=>hit.photo).filter(Boolean);putResult('Matching photos',found);if(found.length)await saveFixedAlbum(text,found.map(photo=>photo.photoId));say(found.length?`Found ${found.length} matching photos using semantic photo search.`:'No photos matched that description.');renderLibrary();return;}
      }
      const photos=await photosByIds(result.photoIds||[]);putResult(result.title||text,photos);say(photos.length?`Found ${photos.length} matching photos. ${plannerNote}`:`No matching photos found. ${plannerNote}`);
    }
  }catch(error){
    say(`That action could not be completed: ${error.message}`);notify(`Your request could not be completed. ${error.message}`,'error');
  }
  if(state.view==='library')renderLibrary();else render();
}

function renderMemories() {
  const day = todayKey();
  const memories = state.photos.filter((photo) => photo.status === 'READY' && !photo.hideMemory && photo.capturedDate && photo.capturedDate.slice(5) === day.slice(5) && photo.capturedDate.slice(0,4) < day.slice(0,4));
  memories.sort((a,b) => (b.favorite===true)-(a.favorite===true) || (b.sharpness||0)-(a.sharpness||0));
  const date = new Intl.DateTimeFormat('en-AU', {day:'numeric',month:'long'}).format(new Date());
  const years = [...new Set(memories.map((photo)=>photo.capturedDate.slice(0,4)))];
  $('#page-content').innerHTML = `<div class="page-inner"><section class="memory-hero"><span class="memory-date">${escapeHtml(date.toLocaleUpperCase())} &nbsp; · &nbsp; A MOMENT TO REMEMBER</span><h1>From the same day, in years gone by.</h1><p>A familiar face, a trip you took, the ordinary afternoons that turned out to be everything. The little pieces of your story, found again.</p></section><div class="section-heading"><div><h2>A page from your past</h2><p>${memories.length ? `${memories.length} photos from ${years.length} ${years.length===1?'year':'years'}` : 'Moments photographed on this day in earlier years'}</p></div></div>${grid(memories)}${memories.length?`<p class="memory-footnote">Dates come from the camera or the date you set. Your memories stay private to your family.</p>`:''}</div>`;
  hydrateImages($('#page-content'));
}

function renderSuggestions() {
  const ready = state.photos.filter((photo)=>photo.status==='READY');
  const keepers = ready.filter((photo)=>photo.keep);
  const candidates = ready.filter((photo)=>!photo.keep);
  const groups = [];
  const seen = new Set();
  for (let i=0; i<candidates.length; i++) {
    const first=candidates[i]; if(seen.has(first.photoId)) continue;
    const others=candidates.slice(i+1).filter((item)=>!seen.has(item.photoId) && item.sha256===first.sha256);
    if(others.length){groups.push({referencePhotoId:first.photoId,matches:others.map((item)=>({photoId:item.photoId,reason:'identical-file'}))});others.forEach((item)=>seen.add(item.photoId));}
  }
  const reviewed=ready.filter((photo)=>photo.qualityWarnings?.length&&!photo.keep);
  const content = `<div class="page-inner"><section class="welcome-row"><div class="welcome-copy"><span class="eyebrow">GENTLE SUGGESTIONS, YOUR CALL</span><h1>A little room to <span class="olive-word">breathe.</span></h1><p>A thoughtful review of your photos. Nothing disappears unless you decide it should.</p></div></section><section class="review-summary"><article class="review-card"><span class="review-kicker">ALMOST EXACT MATCHES</span><h3>${groups.reduce((count,group)=>count+group.matches.length,0)} to compare</h3><p>The same original image may have been added more than once.</p></article><article class="review-card quality"><span class="review-kicker">MIGHT NEED A CLOSER LOOK</span><h3>${reviewed.length} gentle reminders</h3><p>Blur, exposure or screen-size checks. Special moments don't need to be perfect.</p></article></section>
    <div class="section-heading"><div><h2>Same picture, twice?</h2><p>Exact file matches only. Similarity between different photos still needs your eyes.</p></div></div>
    ${groups.length?groups.map((group,index)=>`<section class="suggestion-group"><p class="suggestion-caption">${group.matches.length+1} identical copies · you decide what belongs</p><div class="suggestion-photos">${[group.referencePhotoId,...group.matches.map((item)=>item.photoId)].map((id)=>{const photo=photoById(id);return photo?`<div class="suggestion-item"><img data-photo-id="${escapeHtml(id)}" data-size="320" class="skeleton" alt="" loading="lazy"><p>${escapeHtml(photo.name||friendlyDate(photo.capturedDate))}</p><button class="text-button" data-action="keep" data-id="${escapeHtml(id)}">Keep this photo ♥</button></div>`:''}).join('')}</div><button class="button button-secondary" data-action="review-group" data-index="${index}">Compare all copies</button></section>`).join(''):emptyState('No identical photos found', 'When the very same image appears twice, we’ll bring both copies here so you can choose.')}
    <div class="section-heading quality-heading"><div><h2>A few gentle reminders</h2><p>Small hints, never a verdict. A treasured photo can be imperfect.</p></div></div>
    ${reviewed.length?`<div class="photo-grid">${reviewed.map((photo)=>`${card(photo)}<p class="quality-reason">${escapeHtml(photo.qualityWarnings.join(' · '))}</p>`).join('')}</div>`:emptyState('Nothing needs a second look', 'Our simple checks didn’t notice duplicates or quality hints. Your judgement always comes first.')}</div>`;
  state._reviewGroups=groups;
  $('#page-content').innerHTML=content;
  hydrateImages($('#page-content'));
}

function renderAlbums() {
  const result=state.albumPhotos;
  const saved=state.savedAlbums||[];
  const people=[...new Set(state.photos.flatMap((photo)=>photo.people||[]))].sort();
  $('#page-content').innerHTML=`<div class="page-inner"><section class="welcome-row"><div class="welcome-copy"><span class="eyebrow">A FEW WORDS, A LITTLE ALBUM</span><h1>Gather a <span class="olive-word">story.</span></h1><p>Choose who, when, and what matters. Your album is a collection of shortcuts back to the original photos.</p></div></section><div class="album-layout"><form class="album-panel" id="album-form"><h2>Your next album</h2><p>Start with a person and a date range, or let the optional language model translate a short request into supported filters.</p><label class="field-label">Album name<input class="field-control" name="title" maxlength="100" placeholder="A Sunday in spring" required></label><label class="field-label">Picture this…<textarea class="field-control" name="prompt" rows="3" maxlength="2000" placeholder="Photos of Mum from last summer"></textarea></label><label class="field-label">Person<select class="field-control" name="person"><option value="">Everyone</option>${people.map((person)=>`<option value="${escapeHtml(person)}">${escapeHtml(person)}</option>`).join('')}</select></label><div class="field-row"><label class="field-label">From<input class="field-control" name="since" type="date"></label><label class="field-label">Until<input class="field-control" name="until" type="date"></label></div><label class="field-label">Display shape<select name="orientation" class="field-control"><option value="">Any shape</option><option value="landscape">Wide</option><option value="portrait">Tall</option><option value="square">Square</option></select></label><label class="field-label">How many photos?<input class="field-control" name="limit" type="number" min="1" max="100" value="30"></label><button class="button button-primary button-wide" type="submit">Gather my photos <span>→</span></button></form><section class="album-result" id="album-result">${result?`<div class="album-result-heading"><h3>${escapeHtml(result.title)}</h3><span>${result.photos.length} little moments</span>${result.photos.length?'<button class="button button-secondary" data-action="play-album">Play slideshow</button>':''}</div>${result.photos.length?grid(result.photos):emptyState('This album is empty', 'Removed photos stay in All Photos. Add them to another album whenever you like.')}`:emptyState('Your next chapter goes here', 'Choose a few details and we’ll gather a private album from the photos in this family library.')}</section></div></div>`;
  const guide=document.createElement('p');
  guide.className='album-guide';
  guide.textContent='Name your album, then choose any people, dates or photo shape. “Picture this…” is optional: describe a moment and Stillday will try to find matching photos. Click Gather my photos to save and view the album; choose Play slideshow to replay it later.';
  $('.album-panel').insertBefore(guide,$('.album-panel .field-label'));
  if(result?.albumId){
    const heading=$('#album-result .album-result-heading');
    if(heading){const button=document.createElement('button');button.type='button';button.className='button button-secondary album-edit-button';button.dataset.action='manage-album';button.textContent='Edit name · add photos';heading.append(button);}
  }
  if(saved.length){
    const section=document.createElement('section');
    section.className='saved-album-list';
    section.innerHTML=`<div class="section-heading"><div><h2>Saved albums</h2><p>Open or delete a saved collection. Deleting an album keeps its photos in All Photos.</p></div></div><div class="saved-album-items">${saved.map(album=>`<div class="saved-album-item"><button class="button button-secondary saved-album-open" type="button" data-action="open-album" data-id="${escapeHtml(album.albumId)}">${escapeHtml(album.title)} · ${(album.photoIds||[]).length} photos</button><button class="saved-album-delete" type="button" data-action="delete-album" data-id="${escapeHtml(album.albumId)}" aria-label="Delete album ${escapeHtml(album.title)}" title="Delete album">Delete</button></div>`).join('')}</div>`;
    $('.album-layout').before(section);
  }
  if(result) hydrateImages($('#album-result'));
}

function renderPairs() {
  const available=state.photos.filter((photo)=>photo.status==='READY');
  $('#page-content').innerHTML=`<div class="page-inner"><section class="welcome-row"><div class="welcome-copy"><span class="eyebrow">THEN, AND THE BEAUTIFUL NOW</span><h1>Some things come <span class="olive-word">round again.</span></h1><p>Pick an old photo and its new companion. A little side-by-side reminder of how life changes and stays the same.</p></div></section>${state.pairs.length?state.pairs.map((pair)=>{const old=photoById(pair.originalPhotoId),fresh=photoById(pair.newPhotoId);return old&&fresh?`<article class="pair-card"><h3>Then &amp; now</h3><div class="pair-grid"><div><img data-photo-id="${escapeHtml(old.photoId)}" data-size="1280" alt="" class="skeleton"><div class="pair-caption"><span>Then</span><span>${escapeHtml(friendlyDate(old.capturedDate))}</span></div></div><div><img data-photo-id="${escapeHtml(fresh.photoId)}" data-size="1280" alt="" class="skeleton"><div class="pair-caption"><span>Now</span><span>${escapeHtml(friendlyDate(fresh.capturedDate))}</span></div></div></div></article>`:''}).join(''):''}<div class="section-heading"><div><h2>Choose two photos</h2><p>${state.selectedPair.size} of 2 selected · select real photos for your comparison</p></div></div>${grid(available)}<div class="pair-select-bar"><span>${state.selectedPair.size} photo${state.selectedPair.size===1?'':'s'} selected</span><button class="button button-primary" data-action="create-pair" ${state.selectedPair.size!==2?'disabled':''}>Save the then &amp; now <span>→</span></button></div></div>`;
  hydrateImages($('#page-content'));
}

function renderTrash() {
  $('#page-content').innerHTML=`<div class="page-inner"><section class="welcome-row"><div class="welcome-copy"><span class="eyebrow">ROOM TO CHANGE YOUR MIND</span><h1>Nothing is lost <span class="olive-word">just yet.</span></h1><p>Photos stay in this private recycle bin until you restore them. Storage cleanup is a separate future feature.</p></div></section>${grid(state.photos.filter((photo)=>photo.status==='TRASHED'))}</div>`;
  hydrateImages($('#page-content'));
}

function render() {
  state.pendingRefreshRender = false;
  updateSidebar();
  const content=$('#page-content');
  if(!state.family){state.view='library';renderLibrary();return;}
  const views={library:renderLibrary,memories:renderMemories,suggestions:renderSuggestions,album:renderAlbums,compare:renderPairs,trash:renderTrash};
  (views[state.view]||renderLibrary)();
  if(state.view!=='library'){
    const ready=state.photos.filter(photo=>photo.status==='READY').length;
    const messages=state.chatMessages.length?state.chatMessages:[{role:'assistant',text:`Your library has ${ready} ready photos. Ask me to operate the photo tools.`}];
    const panel=`<div class="chat-page persistent-chat"><header class="chat-heading"><div><span class="eyebrow">PHOTO LIBRARY TOOLS</span><h1>What would you like to do?</h1></div></header><section class="chat-panel"><div class="chat-thread" aria-live="polite">${messages.map(message=>`<div class="chat-message ${message.role==='user'?'user-message':message.role==='tool'?'tool-message':'assistant-message'}"><span class="chat-avatar">${message.role==='user'?'You':message.role==='tool'?'Tool':'S'}</span><div class="chat-bubble">${escapeHtml(message.text)}</div></div>`).join('')}</div><form class="chat-composer" id="chat-form"><label class="visually-hidden" for="chat-input">Ask about your photos</label><textarea id="chat-input" rows="1" maxlength="2000" placeholder="Ask me to find, organise or play photos..." required></textarea><button class="chat-send" type="submit" aria-label="Send message">↑</button></form></section></div>`;
    $('#page-content').insertAdjacentHTML('afterbegin',panel);
  }
  const chatInput=$('#chat-input');
  if(chatInput&&document.activeElement?.id!=='chat-input')chatInput.value=state.chatDraft;
  persistUiState();
}

async function refreshLibrary({quiet=false}={}) {
  if(state.refreshing)return false;
  state.refreshing=true;
  const refreshButton=$('#refresh');
  if(!quiet)setBusy(refreshButton,true,'Refreshing...');
  if(!state.family){state.refreshing=false;state.photos=[];render();if(!quiet)setBusy(refreshButton,false);return true;}
  const select=$('#family-select');
  if(select&&! [...select.options].some((option)=>option.value===state.family)){
    const option=document.createElement('option');option.value=state.family;option.textContent='Family album';select.add(option);
  }
  try{
    const result=await api(`/a3/families/${encodeURIComponent(state.family)}/photos?trash=true`);
    const responsePhotos=Array.isArray(result)?result:[];
    const responseIds=new Set(responsePhotos.map((photo)=>photo.photoId));
    const optimisticPhotos=state.photos.filter((photo)=>state.pendingUploads.has(photo.photoId)&&!responseIds.has(photo.photoId));
    const next=[...responsePhotos,...optimisticPhotos];
    const signature=items=>JSON.stringify([...items].sort((a,b)=>a.photoId.localeCompare(b.photoId)));
    const changed=signature(next)!==signature(state.photos);
    const previous=new Map(state.photos.map((photo)=>[photo.photoId,photo.status]));
    state.photos=next;
    for(const photo of next){
      if(['QUEUED','PROCESSING','QUEUE_FAILED','READY','FAILED','TRASHED'].includes(photo.status))state.uploadStages.delete(photo.photoId);
      if(['READY','FAILED','TRASHED'].includes(photo.status))state.pendingUploads.delete(photo.photoId);
    }
    next.filter((photo)=>photo.status==='READY').forEach((photo)=>{
      const key=`${photo.photoId}:preview`;
      if(state.objectUrls.has(key)){URL.revokeObjectURL(state.objectUrls.get(key));state.objectUrls.delete(key);}
    });
    const newlyReady=next.filter((photo)=>photo.status==='READY'&&previous.has(photo.photoId)&&previous.get(photo.photoId)!=='READY');
    if(newlyReady.length)state.chatMessages.push({role:'tool',text:`Background processing complete: ${newlyReady.map((photo)=>photo.name||'photo').join(', ')} ready to view.`});
    $('#connection-panel').classList.add('hidden');
    $('#sync-status').innerHTML='<span class="status-dot"></span> Your library is up to date';
    // Quiet worker polling must not replace an editor while the user is typing.
    // State is still refreshed above; defer only the DOM update until the field blurs.
    const active=document.activeElement;
    const editing=quiet&&active?.closest('#page-content')&&['INPUT','TEXTAREA','SELECT'].includes(active.tagName);
    if(changed){if(editing){state.pendingRefreshRender=true;updateSidebar();}else if(state.uploadBusy)renderLibrary();else render();}
    if(newlyReady.length)notify(`${newlyReady.length} ${newlyReady.length===1?'photo is':'photos are'} ready to revisit.`);
    if(!quiet)notify('Your family album is up to date.');
    if(state.timer)window.clearInterval(state.timer);
    state.timer=null;
    if(next.some((photo)=>['UPLOADING','QUEUED','PROCESSING'].includes(photo.status))){
      $('#sync-status').innerHTML='<span class="status-dot working"></span> Photos are queued or processing. Checking every 5 seconds...';
      state.timer=window.setInterval(()=>refreshLibrary({quiet:true}),5000);
    }
    return true;
  }catch(error){
    $('#connection-panel').classList.remove('hidden');
    $('#connection-panel').innerHTML=`<span>${escapeHtml(error.message)}${error.message.includes('permission')||error.message.includes('token')?' You can switch accounts using the profile button.':''}</span><button data-action="refresh">Try again</button><button data-action="connect">Sign in</button>`;
    $('#sync-status').textContent='Refresh failed — displayed photos may be out of date';
    if(!quiet)notify(`Could not refresh the library. ${error.message}`,'error');
    return false;
  }finally{state.refreshing=false;if(!quiet)setBusy(refreshButton,false);}
}

function showAccess() { openDialog('access-dialog'); $('#login-username').focus(); }
function loginStatus(message, kind='info') {
  const panel=$('#login-status');
  panel.className=`login-status ${kind}`;
  panel.textContent=message;
  panel.setAttribute('role',kind==='error'?'alert':'status');
  if(kind==='error'){panel.focus();panel.scrollIntoView({block:'nearest'});}
}
async function signIn(event) {
  event.preventDefault();
  const button=event.target.querySelector('button[type="submit"]');
  if(button.disabled)return;
  const username=$('#login-username').value.trim(), password=$('#login-password');
  if(!username || !password.value){loginStatus(!username?'Please enter your username or email.':'Please enter your password.','error');return;}
  setBusy(button,true,'Signing in...');
  loginStatus('Contacting the sign-in service. Please wait (up to 30 seconds)...');
  try {
    const result=await request('https://cognito-idp.ap-southeast-2.amazonaws.com/',{
      method:'POST',headers:{'Content-Type':'application/x-amz-json-1.1','X-Amz-Target':'AWSCognitoIdentityProviderService.InitiateAuth'},
      body:JSON.stringify({AuthFlow:'USER_PASSWORD_AUTH',ClientId:'5526cls2cc5k0c6gikp2encaar',AuthParameters:{USERNAME:username,PASSWORD:password.value}})
    },30000,async response=>{
      const data=await response.json();
      if(!response.ok){
        const code=(data.__type||'').split('#').pop();
        const reasons={NotAuthorizedException:'The username or password is incorrect, or the account cannot sign in. Check your details.',UserNotFoundException:'The username or password is incorrect. Check your details.',UserNotConfirmedException:'Your account has not been confirmed. Complete account verification first.',PasswordResetRequiredException:'Your password must be reset. Contact the account administrator.',TooManyRequestsException:'Too many sign-in attempts. Wait a moment before retrying.',InvalidParameterException:'The login configuration rejected this request. Contact the project owner.'};
        throw new Error(reasons[code]||`The login service could not complete the request (HTTP ${response.status}). Try again later or contact the project owner.`);
      }return data;
    });
    if(!result.AuthenticationResult?.AccessToken)throw new Error(result.ChallengeName==='NEW_PASSWORD_REQUIRED'?'Your account requires a new password. Complete the password change with your account administrator.':'Your account requires an additional verification step that this form does not yet support. Contact your account administrator.');
    state.token=result.AuthenticationResult.AccessToken;sessionStorage.setItem(settings.tokenKey,state.token);
    state.user=username;localStorage.setItem(settings.userKey,username);
    state.photos=[];state.pairs=[];state.albumPhotos=null;state.chatMessages=[];
    for(const url of state.objectUrls.values())URL.revokeObjectURL(url);
    state.objectUrls.clear();state.signedImages.clear();state.pendingUploads.clear();if(state.timer)clearInterval(state.timer);
    loginStatus('Your credentials were accepted. Loading your photo library...');
    const loaded=await refreshLibrary({quiet:true});
    closeDialog('access-dialog');
    notify(loaded?'Signed in successfully. You can now use your photo library.':'Signed in successfully, but the selected family album could not be loaded. Check the connection message and your family access.',loaded?'success':'info');
  }catch(error){loginStatus(`Sign-in failed. ${error.message}`,'error');}
  finally{password.value='';setBusy(button,false);}
}

function openDialog(id) { const element=$(`#${id}`);element?.querySelector('.inline-notice')?.remove();if(element?.showModal)element.showModal(); }
function closeDialog(id) { $(`#${id}`)?.close(); }
function confirmTrash(photo, mode='trash') {
  const dialog=$('#trash-confirm-dialog');
  const removingFromAlbum=mode==='album';
  $('#trash-confirm-title').textContent=removingFromAlbum?'Remove photo from this album?':'Move photo to Recently deleted';
  $('#trash-confirm-name').textContent=photo.name||'Untitled photo';
  $('#trash-confirm-detail').textContent=removingFromAlbum?' will remain in All Photos.':' can be restored later.';
  $('#trash-confirm-submit').textContent=removingFromAlbum?'Remove from album':'Move photo';
  dialog.returnValue='';
  return new Promise((resolve)=>{
    const onClose=()=>{dialog.removeEventListener('close',onClose);resolve(dialog.returnValue==='move');};
    dialog.addEventListener('close',onClose);
    dialog.showModal();
  });
}

function confirmAlbumDelete(album) {
  const dialog=$('#trash-confirm-dialog');
  $('#trash-confirm-title').textContent='Delete saved album?';
  $('#trash-confirm-name').textContent=album.title;
  $('#trash-confirm-detail').textContent=' will be removed. Its photos remain in All Photos.';
  $('#trash-confirm-submit').textContent='Delete album';
  dialog.returnValue='';
  return new Promise((resolve)=>{
    const onClose=()=>{dialog.removeEventListener('close',onClose);resolve(dialog.returnValue==='move');};
    dialog.addEventListener('close',onClose);
    dialog.showModal();
  });
}

async function createFamily(name, button) {
  setBusy(button,true,'Making space…');
  try{
    const family=await api('/a3/families',{method:'POST',body:{name}});
    state.family=family.familyId;localStorage.setItem(settings.familyKey,state.family);
    localStorage.setItem(`stillday.family-name.${state.family}`,family.name);
    closeDialog('family-dialog');state.photos=[];await refreshLibrary({quiet:true});notify('Family album created. You can now upload photos.');
  }catch(error){notify(error.message,'error');}finally{setBusy(button,false);}
}

async function uploadFiles(files, button) {
  if(state.uploadBusy){notify('An upload batch is already running. Wait for its summary before adding more photos.','info');return;}
  if(!state.family){openDialog('family-dialog');notify('Create or select a family album before uploading photos.','info');return;}
  const selected=[...files];
  if(!selected.length)return;
  state.view='library';
  state.albumPhotos=null;
  state.uploadBusy=true;
  setBusy(button,true,'Uploading...');
  const progress=notify(`Preparing ${selected.length} photo(s).`,'progress');
  const activity={role:'tool',text:'Preparing photo uploads...'};
  state.chatMessages.push(activity);
  renderLibrary();
  const updateActivity=()=>{
    const bubble=$('#chat-thread .tool-message:last-child .chat-bubble');
    if(bubble)bubble.textContent=activity.text;
  };
  let success=0;
  const failures=[];
  try {
    for (const [index,file] of selected.entries()) {
      let stage='Checking file';
      let photoId=null;
      try {
        if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw new Error('Unsupported format. Choose JPEG, PNG or WebP.');
        if(!file.size || file.size>20*1024*1024)throw new Error('File must be larger than 0 bytes and no more than 20 MB.');
        const show=(step)=>{stage=step;activity.text=`${index+1}/${selected.length}: ${file.name} — ${step}`;progress.update(activity.text);updateActivity();if(photoId){state.uploadStages.set(photoId,step);renderLibrary();}};
        show('Requesting upload permission');
        const created=await api(`/a3/families/${encodeURIComponent(state.family)}/uploads`,{method:'POST',body:{name:file.name,mime:file.type}});
        photoId=created.photo.photoId;
        const optimisticPhoto={...created.photo,name:created.photo.name||file.name,status:'UPLOADING'};
        state.photos=[optimisticPhoto,...state.photos.filter((photo)=>photo.photoId!==photoId)];
        state.pendingUploads.add(photoId);
        state.objectUrls.set(`${photoId}:preview`,URL.createObjectURL(file));
        state.uploadStages.set(photoId,'Uploading original to private storage');
        updateSidebar();
        startPhotoStatusPolling();
        renderLibrary();
        const receipt=created.upload;
        show('Uploading file');
        let completedPhoto;
        if(receipt.fields){
          const body=new FormData();Object.entries(receipt.fields).forEach(([key,value])=>body.append(key,value));body.append('file',file);
          const posted=await request(receipt.url,{method:'POST',body},120000);
          if(!posted.ok)throw requestFailure(posted.status,posted.status===403?'The temporary upload permission was rejected or expired.':'Photo storage did not accept the upload.');
          show('Upload saved; queueing background image processing');
          completedPhoto=await api(`${photoPath(created.photo)}/complete`,{method:'POST'});
        }else{
          show('Uploading original to private storage');
          completedPhoto=await api(receipt.url,{method:'PUT',body:file,headers:{'Content-Type':file.type}});
        }
        const queuedPhoto=completedPhoto?.photoId?completedPhoto:{...optimisticPhoto,status:'QUEUED'};
        state.photos=state.photos.map((photo)=>photo.photoId===photoId?{...photo,...queuedPhoto}:photo);
        state.uploadStages.delete(photoId);
        renderLibrary();
        success++;
      }catch(error){
        if(photoId){
          state.uploadStages.set(photoId,`Upload not confirmed during ${stage.toLocaleLowerCase()}. ${error.message} Refresh to check its status before retrying.`);
          renderLibrary();
        }
        failures.push(`${file.name} — ${stage}: ${error.message}`);
      }
    }
    activity.text=`Upload results: ${success} queued for processing; ${failures.length} not confirmed. Processing is separate from uploading.`;
    if(failures.length)activity.text+=' '+failures.join(' | ');
    updateActivity();
    await refreshLibrary({quiet:true});
    if(success)notify(`${success} photo(s) uploaded and queued. Processing is not finished yet; this page checks automatically. Starting a worker can take a few minutes.`,'info');
    if(failures.length)notify(`${success} of ${selected.length} photo(s) queued. ${failures.length} could not be confirmed.\n${failures.join('\n')}`,'error');
  }finally{
    progress.close();state.uploadBusy=false;setBusy(button,false);
    const input=$('#file-input');if(input)input.value='';
    if(state.pendingRefreshRender&&state.view==='library'){state.pendingRefreshRender=false;render();}
  }
}

async function updatePhoto(photoId, changes, success='Photo details saved.') {
  await api(`${photoPath({photoId})}`,{method:'PATCH',body:changes});
  forgetImages(photoId);await refreshLibrary({quiet:true});if(success)notify(success);
}

async function trashPhoto(photoId) {
  const photo=photoById(photoId);if(!photo||!await confirmTrash(photo))return;
  await api(`${photoPath({photoId})}/trash`,{method:'POST'});await refreshLibrary({quiet:true});notify('Moved to recently deleted. You can still restore it.');
}

async function removePhotoFromAlbum(photoId) {
  const album=state.albumPhotos;
  const photo=album?.photos?.find((item)=>item.photoId===photoId);
  if(!album?.albumId||!photo||!await confirmTrash(photo,'album'))return;
  const result=await api(`/a3/families/${encodeURIComponent(state.family)}/albums/${encodeURIComponent(album.albumId)}/photos/${encodeURIComponent(photoId)}`,{method:'DELETE'});
  album.photos=album.photos.filter((item)=>item.photoId!==photoId);
  album.photoIds=result.photoIds||album.photos.map((item)=>item.photoId);
  state.savedAlbums=(state.savedAlbums||[]).map((item)=>item.albumId===album.albumId?{...item,photoIds:album.photoIds}:item);
  renderAlbums();notify('Removed from this album. The photo remains in All Photos.');
}

async function deleteSavedAlbum(albumId) {
  const album=state.savedAlbums?.find((item)=>item.albumId===albumId);
  if(!album||!await confirmAlbumDelete(album))return;
  await api(`/a3/families/${encodeURIComponent(state.family)}/albums/${encodeURIComponent(albumId)}`,{method:'DELETE'});
  state.savedAlbums=state.savedAlbums.filter((item)=>item.albumId!==albumId);
  if(state.albumPhotos?.albumId===albumId)state.albumPhotos=null;
  renderAlbums();notify('Album deleted. Its photos remain in All Photos.');
}

async function restorePhoto(photoId) {
  await api(`${photoPath({photoId})}/restore`,{method:'POST'});await refreshLibrary({quiet:true});notify('This photo is back in your collection.');
}

function editPhoto(photo) {
  if(!photo)return;
  $('#metadata-photo-id').value=photo.photoId;
  $('#metadata-uploaded').textContent=uploadDate(photo.uploadedAt);
  $('#metadata-people').value=(photo.people||[]).join(', ');
  $('#metadata-date').value=photo.capturedDate||'';
  $('#metadata-favourite').checked=!!photo.favorite;
  openDialog('metadata-dialog');
}

async function createAlbum(form) {
  const data=new FormData(form);
  const filters={limit:Number(data.get('limit'))||30};
  for(const key of ['person','since','until','orientation'])if(data.get(key))filters[key]=data.get(key);
  const prompt=data.get('prompt')?.toString().trim();
  const result=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`,{method:'POST',body:{title:data.get('title'),filters,...(prompt?{prompt}:{})}});
  state.albumPhotos={...result,photos:await photosByIds(result.photoIds||[])};
  state.savedAlbums=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`);
  renderAlbums();notify(`${state.albumPhotos.photos.length} photos gathered into your album.`);
}

async function saveFixedAlbum(title, photoIds) {
  const ids=[...new Set(photoIds)].slice(0,100);
  if(!state.family||!ids.length)return null;
  const album=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`,{method:'POST',body:{title:title.slice(0,100)||'Photo search',photoIds:ids}});
  state.albumPhotos={...album,photos:await photosByIds(album.photoIds||ids)};
  state.savedAlbums=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`);
  return album;
}

async function openSavedAlbum(id) {
  const album=await api(`/a3/families/${encodeURIComponent(state.family)}/albums/${encodeURIComponent(id)}`);
  state.albumPhotos={...album,photos:await photosByIds((album.photos||[]).map(photo=>photo.photoId))};
  renderAlbums();
}

async function manageSavedAlbum() {
  const album=state.albumPhotos;
  if(!album?.albumId)return;
  const existing=new Set(album.photos.map(photo=>photo.photoId));
  const available=state.photos.filter(photo=>photo.status==='READY'&&!existing.has(photo.photoId));
  const dialog=document.createElement('dialog');
  dialog.id='album-edit-dialog';dialog.className='dialog album-edit-dialog';
  dialog.innerHTML=`<form id="album-edit-form"><button type="button" class="dialog-close" data-close="album-edit-dialog" aria-label="Close">×</button><div class="dialog-icon">✎</div><p class="eyebrow">SAVED ALBUM</p><h2>Edit album</h2><p class="dialog-copy">Rename the album and add photos. Photos you add stay in All Photos too.</p><label class="album-edit-name">Album name<input name="title" maxlength="100" required value="${escapeHtml(album.title)}"></label><fieldset class="album-add-fieldset"><legend>Add photos from All Photos</legend><div class="album-add-picker">${available.length?available.map(photo=>`<label class="album-add-choice"><input type="checkbox" name="photoIdsToAdd" value="${escapeHtml(photo.photoId)}"><img data-photo-id="${escapeHtml(photo.photoId)}" data-size="320" alt="" loading="lazy"><span><strong>${escapeHtml(photo.name||'Untitled photo')}</strong><small>${escapeHtml(photo.orientation||'')} · ${escapeHtml(friendlyDate(photo.capturedDate))}</small></span></label>`).join(''):'<p class="album-add-empty">All ready photos are already in this album.</p>'}</div></fieldset><div class="album-editor-actions"><button type="button" class="button button-secondary" data-close="album-edit-dialog">Cancel</button><button type="submit" class="button button-primary">Save changes</button></div></form>`;
  document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.querySelector('#album-edit-form').addEventListener('submit',async(event)=>{
    event.preventDefault();
    const form=event.currentTarget,button=form.querySelector('[type="submit"]');
    const title=form.elements.title.value.trim();
    const photoIdsToAdd=[...form.querySelectorAll('input[name="photoIdsToAdd"]:checked')].map(input=>input.value);
    if(!title){form.elements.title.focus();return;}
    if(album.photos.length+photoIdsToAdd.length>100){notify('An album can contain at most 100 photos.','error');return;}
    button.disabled=true;
    try{
      const updated=await api(`/a3/families/${encodeURIComponent(state.family)}/albums/${encodeURIComponent(album.albumId)}`,{method:'PATCH',body:{title,photoIdsToAdd}});
      state.albumPhotos={...album,...updated,photos:await photosByIds(updated.photoIds||[])};
      state.savedAlbums=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`);
      dialog.close();renderAlbums();notify('Album updated.');
    }catch(error){notify(error.message,'error');button.disabled=false;}
  });
  hydrateImages(dialog);dialog.showModal();
}

function togglePair(id) {
  if(photoById(id)?.status!=='READY')return;
  if(state.selectedPair.has(id))state.selectedPair.delete(id);
  else if(state.selectedPair.size<2)state.selectedPair.add(id);
  else{notify('Choose two photos. Deselect one to replace it.','info');return;}
  $$('.pair-selector').forEach(button=>{
    const order=[...state.selectedPair].indexOf(button.dataset.id);
    button.setAttribute('aria-pressed',String(order>=0));
    button.textContent=order<0?'Select photo':`Selected ${order+1} · ${order===0?'Then':'Now'}`;
    button.closest('.photo-card')?.classList.toggle('pair-selected',order>=0);
  });
  const bar=$('.pair-select-bar');
  if(bar){$('span',bar).textContent=`${state.selectedPair.size} of 2 selected · first: Then, second: Now`;$('button',bar).disabled=state.selectedPair.size!==2;}
}

function currentAlbumIds() {
  const source=state.albumPhotos!==null?state.albumPhotos.photos:currentPhotos();
  return source.filter(photo=>photoById(photo.photoId)?.status==='READY').map(photo=>photo.photoId);
}

function playCurrentAlbum() {
  const ids=currentAlbumIds();
  if(!ids.length){notify('This selection has no ready photos to play.','info');return;}
  renderPlayer(ids[0],ids);
}

async function createPair() {
  const [originalPhotoId,newPhotoId]=[...state.selectedPair];
  if(!originalPhotoId||!newPhotoId){notify('Choose two photos for your comparison.','error');return;}
  await api(`/a3/families/${encodeURIComponent(state.family)}/recreations`,{method:'POST',body:{originalPhotoId,newPhotoId}});
  state.selectedPair.clear();await loadPairs();renderPairs();notify('Your then-and-now pair is ready.');
}

async function loadPairs() {
  state.pairs=await api(`/a3/families/${encodeURIComponent(state.family)}/recreations`);
}

function renderPlayer(startId, photoIds=null, {autoplay=false,fullscreen=false}={}) {
  document.querySelector('.player-overlay')?.closePlayer?.();
  const first=photoById(startId);
  const ids=photoIds??(first?.status==='TRASHED'?[startId]:currentAlbumIds());
  const photos=[...new Set(ids)].map(photoById).filter(photo=>photo&&['READY','TRASHED'].includes(photo.status));
  if(!photos.length){notify('This selection has no available photos.','info');return;}
  let index=Math.max(0,photos.findIndex(photo=>photo.photoId===startId));
  let timer=null,playing=autoplay,version=0,closed=false;
  const previousFocus=document.activeElement;
  const overlay=document.createElement('div');overlay.className='player-overlay';
  overlay.setAttribute('role','dialog');overlay.setAttribute('aria-modal','true');overlay.setAttribute('aria-label','Photo slideshow');
  overlay.innerHTML=`<div class="player-top"><span class="player-brand">stillday.</span><button class="player-fullscreen">Full screen</button><button class="player-close" aria-label="Close slideshow">×</button></div><button class="player-arrow previous" aria-label="Previous photo">‹</button><figure class="player-figure"><div class="player-stage"><div class="player-skeleton">Loading photo…</div><img alt="" class="player-image"></div><figcaption class="player-caption"></figcaption></figure><button class="player-arrow next" aria-label="Next photo">›</button><div class="player-bottom"><span class="player-counter"></span><button class="player-play"></button><input class="player-progress" type="range" min="0" max="${photos.length-1}" value="0" step="1" aria-label="Slideshow position"></div>`;
  document.body.append(overlay);
  const image=$('.player-image',overlay),skeleton=$('.player-skeleton',overlay),play=$('.player-play',overlay),stage=$('.player-stage',overlay);
  const fitImage=()=>{
    const maxWidth=stage.clientWidth,maxHeight=stage.clientHeight;
    if(!image.naturalWidth||!image.naturalHeight||!maxWidth||!maxHeight)return;
    const scale=Math.min(maxWidth/image.naturalWidth,maxHeight/image.naturalHeight);
    image.style.width=`${Math.floor(image.naturalWidth*scale)}px`;
    image.style.height=`${Math.floor(image.naturalHeight*scale)}px`;
  };
  const stageObserver=typeof ResizeObserver==='undefined'?null:new ResizeObserver(fitImage);
  stageObserver?.observe(stage);window.addEventListener('resize',fitImage,{passive:true});
  const schedule=()=>{clearTimeout(timer);if(playing&&!closed&&photos.length>1)timer=setTimeout(()=>step(1),4500);};
  const updatePlay=()=>{play.textContent=playing?'Pause slideshow':'Play slideshow';};
  const draw=async()=>{
    clearTimeout(timer);const ticket=++version;const photo=photos[index];
    image.classList.remove('loaded');image.style.removeProperty('width');image.style.removeProperty('height');skeleton.style.display='block';skeleton.textContent='Loading photo…';
    $('.player-counter',overlay).textContent=`${index+1} of ${photos.length}`;
    $('.player-progress',overlay).value=String(index);
    $('.player-caption',overlay).textContent=photo.name||'Family photo';
    try{
      const url=await imageUrl(photo,'2560');if(closed||ticket!==version)return;
      image.onload=()=>{if(ticket!==version||closed)return;stage.dataset.orientation=image.naturalHeight>image.naturalWidth?'portrait':image.naturalWidth>image.naturalHeight?'landscape':'square';skeleton.style.display='none';image.classList.add('loaded');requestAnimationFrame(fitImage);schedule();};
      image.onerror=()=>{if(ticket!==version||closed)return;skeleton.textContent='Photo unavailable. Use Next to continue.';schedule();};
      image.alt=photo.name||'Family photo';image.src=url;
      if(image.complete&&image.naturalWidth)image.onload();
    }catch(error){if(ticket!==version||closed)return;skeleton.textContent=error.message;schedule();}
  };
  const step=amount=>{index=(index+amount+photos.length)%photos.length;draw();};
  const fullButton=$('.player-fullscreen',overlay);
  const fullChanged=()=>{fullButton.textContent=document.fullscreenElement===overlay?'Exit full screen':'Full screen';requestAnimationFrame(fitImage);};
  const toggleFull=async()=>{try{if(document.fullscreenElement===overlay)await document.exitFullscreen();else if(overlay.requestFullscreen)await overlay.requestFullscreen();else notify('Full screen is unavailable in this browser.','info');}catch(error){notify('Use the Full screen button to enter fullscreen.','info');}};
  const close=()=>{if(closed)return;closed=true;++version;clearTimeout(timer);stageObserver?.disconnect();window.removeEventListener('resize',fitImage);image.onload=null;image.onerror=null;document.removeEventListener('keydown',keys);document.removeEventListener('fullscreenchange',fullChanged);if(document.fullscreenElement===overlay)document.exitFullscreen().catch(()=>{});overlay.remove();previousFocus?.isConnected&&previousFocus.focus({preventScroll:true});};
  const keys=event=>{if(event.key==='Escape')close();else if(event.key==='ArrowLeft')step(-1);else if(event.key==='ArrowRight')step(1);else if(event.key===' '){event.preventDefault();play.click();}};
  overlay.closePlayer=close;
  $('.player-close',overlay).onclick=close;$('.previous',overlay).onclick=()=>step(-1);$('.next',overlay).onclick=()=>step(1);
  play.onclick=()=>{playing=!playing;updatePlay();if(playing)schedule();else clearTimeout(timer);};
  fullButton.onclick=toggleFull;
  $('.player-progress',overlay).oninput=event=>{index=Number(event.target.value);draw();};
  document.addEventListener('keydown',keys);document.addEventListener('fullscreenchange',fullChanged);
  updatePlay();draw();$('.player-close',overlay).focus();if(fullscreen)toggleFull();
}

document.addEventListener('click',async(event)=>{
  const close=event.target.closest('[data-close]');if(close){closeDialog(close.dataset.close);return;}
  const action=event.target.closest('[data-action]');if(!action)return;
  try{
    switch(action.dataset.action){
      case 'new-family':openDialog('family-dialog');break;
      case 'upload':await submitChat('Upload photos');break;
      case 'upload-files':if(!state.family)openDialog('family-dialog');else $('#file-input').click();break;
      case 'view-memories':navigate('memories');break;
      case 'view-suggestions':navigate('suggestions');break;
      case 'chat-today':await submitChat('Show me photos from this day in history');break;
      case 'chat-review':await submitChat('Find duplicates and photos that need review');break;
      case 'chat-play':playCurrentAlbum();break;
      case 'chat-family':await submitChat('Manage family access');break;
      case 'resolve-clarification':await submitChat(action.dataset.prompt);break;
      case 'reanalyze-photos':{
        const result=await api(`/a3/families/${encodeURIComponent(state.family)}/reanalyze`,{method:'POST',body:{}});
        await refreshLibrary({quiet:true});
        const pending=state.photos.some(photo=>['QUEUED','PROCESSING'].includes(photo.status));
        notify(result.failed?`${result.failed} photo(s) could not be queued. Retry after checking the service connection.`:result.queued?`Queued ${result.queued} photos for caption, label and vector analysis. They will become searchable as the worker finishes.`:pending?'Photo analysis is already in progress. The library refreshes automatically.':'All ready photos already have captions and semantic vectors.',result.failed?'error':'info');
        break;
      }
      case 'clear-chat-results':state.albumPhotos=null;renderLibrary();break;
      case 'save-results-as-album':{
        const current=state.albumPhotos;
        if(current?.photos?.length){await saveFixedAlbum(current.title,current.photos.map(photo=>photo.photoId));renderLibrary();notify('Search results saved. Open Albums to play them later.');}
        break;
      }
      case 'grid':state.grid=true;render();break;
      case 'list':state.grid=false;render();break;
      case 'preview':if(action.dataset.id)renderPlayer(action.dataset.id);break;
      case 'pair-toggle':togglePair(action.dataset.id);break;
      case 'edit':editPhoto(photoById(action.dataset.id));break;
      case 'trash':await trashPhoto(action.dataset.id);break;
      case 'remove-album-photo':await removePhotoFromAlbum(action.dataset.id);break;
      case 'manage-album':await manageSavedAlbum();break;
      case 'delete-album':await deleteSavedAlbum(action.dataset.id);break;
      case 'restore':await restorePhoto(action.dataset.id);break;
      case 'keep':await updatePhoto(action.dataset.id,{keep:true},'We’ll leave this one out of review suggestions.');break;
      case 'reconsider':await updatePhoto(action.dataset.id,{keep:false},'This photo is back in your review suggestions.');break;
      case 'play-album':playCurrentAlbum();break;
      case 'open-album':await openSavedAlbum(action.dataset.id);break;
      case 'create-pair':await createPair();break;
      case 'refresh':await refreshLibrary();break;
      case 'identity':state.mode==='local'?openDialog('identity-dialog'):showAccess();break;
      case 'share':openDialog('members-dialog');break;
      case 'connect':showAccess();break;
      case 'dismiss-access':$('#connection-panel').classList.add('hidden');break;
    }
  }catch(error){notify(error.message,'error');}
});

async function navigate(view){state.view=view;if(view==='library'){state.albumPhotos=null;state.chatDraft='';}if(view!=='suggestions'&&view!=='album')state._reviewGroups=[];if(view==='album'&&state.family){try{state.savedAlbums=await api(`/a3/families/${encodeURIComponent(state.family)}/albums`);}catch(error){notify(error.message,'error');}}if(view==='compare'&&state.family){try{await loadPairs();}catch(error){notify(error.message,'error');}}render();if(innerWidth<760)$('#sidebar').classList.remove('open');}

$('#new-family').addEventListener('click',()=>openDialog('family-dialog'));
$('.brand').addEventListener('click',(event)=>{event.preventDefault();navigate('library');});
$('#share-family').addEventListener('click',async()=>{try{const membership=await api(`/a3/families/${encodeURIComponent(state.family)}/photos`);void membership;openDialog('members-dialog');}catch(error){notify(error.message,'error');}});
$('#refresh').addEventListener('click',()=>refreshLibrary());
$('#mobile-menu').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
$('#mobile-profile').addEventListener('click',()=>{if(state.mode==='local')openDialog('identity-dialog');else if(state.token==='local-development-only')showAccess();else openDialog('signout-confirm-dialog');});
function signOut(){sessionStorage.removeItem(settings.tokenKey);state.token='local-development-only';state.user='local-owner';localStorage.removeItem(settings.userKey);state.photos=[];state.pairs=[];state.albumPhotos=null;state.chatMessages=[];state.pendingUploads.clear();state.uploadStages.clear();if(state.timer)clearInterval(state.timer);state.timer=null;for(const url of state.objectUrls.values())URL.revokeObjectURL(url);state.objectUrls.clear();state.signedImages.clear();render();notify('You have signed out. Select Sign in when you are ready to reconnect.','info');}
$('#identity-toggle').addEventListener('click',()=>openDialog('identity-dialog'));
$('#confirm-signout').addEventListener('click',()=>{closeDialog('signout-confirm-dialog');signOut();});
$('#family-select').addEventListener('change',async(event)=>{state.family=event.target.value;localStorage.setItem(settings.familyKey,state.family);state.albumPhotos=null;state.selectedPair.clear();await refreshLibrary({quiet:true});});
$$('.nav-link').forEach((button)=>button.addEventListener('click',()=>navigate(button.dataset.view)));

$('#family-form').addEventListener('submit',(event)=>{event.preventDefault();createFamily($('#family-name').value.trim(),$('button[type="submit"]',event.currentTarget));});
$('#member-form').addEventListener('submit',async(event)=>{event.preventDefault();const button=$('button[type="submit"]',event.currentTarget);setBusy(button,true,'Welcoming them…');try{await api(`/a3/families/${encodeURIComponent(state.family)}/members`,{method:'POST',body:{userId:$('#member-id').value.trim(),role:$('#member-role').value}});closeDialog('members-dialog');$('#member-id').value='';notify('They are part of your family album now.');}catch(error){notify(error.message,'error');}finally{setBusy(button,false);}});
$('#metadata-form').addEventListener('submit',async(event)=>{
  event.preventDefault();
  const button=$('button[type="submit"]',event.currentTarget);
  if(button.disabled)return;
  setBusy(button,true,'Saving details...');
  const id=$('#metadata-photo-id').value;
  const changes={people:$('#metadata-people').value.split(',').map(name=>name.trim()).filter(Boolean),favorite:$('#metadata-favourite').checked};
  if($('#metadata-date').value)changes.capturedDate=$('#metadata-date').value;
  try{await updatePhoto(id,changes);closeDialog('metadata-dialog');}
  catch(error){notify(`Could not save photo details. ${error.message}`,'error');}
  finally{setBusy(button,false);}
});
$('#identity-form').addEventListener('submit',async(event)=>{event.preventDefault();const user=$('#identity-name').value.trim();if(!/^[a-zA-Z0-9_-]{1,64}$/.test(user)){notify('Use letters, numbers, underscores or hyphens.','error');return;}state.user=user;localStorage.setItem(settings.userKey,user);closeDialog('identity-dialog');await refreshLibrary({quiet:true});notify(`You are now viewing this family as ${user}.`);});
$('#page-content').addEventListener('submit',async(event)=>{if(event.target.id==='album-form'){event.preventDefault();try{await createAlbum(event.target);}catch(error){notify(error.message,'error');}}if(event.target.id==='chat-form'){event.preventDefault();const input=$('#chat-input');const prompt=input.value;state.chatDraft='';input.value='';await submitChat(prompt);}});
$('#access-dialog-form').addEventListener('submit',signIn);
$('#connection-panel').addEventListener('click',(event)=>{const button=event.target.closest('[data-action]');if(!button)return;if(button.dataset.action==='dismiss-access')$('#connection-panel').classList.add('hidden');});
$('#page-content').addEventListener('input',(event)=>{if(event.target.id==='chat-input'){state.chatDraft=event.target.value;}if(event.target.id==='photo-search'){state.query=event.target.value;const cursor=event.target.selectionStart;renderLibrary();$('#photo-search').focus();$('#photo-search').setSelectionRange(cursor,cursor);}});
document.addEventListener('focusout',(event)=>{
  if(!state.pendingRefreshRender||!event.target.closest?.('#page-content'))return;
  window.setTimeout(()=>{
    const active=document.activeElement;
    if(state.pendingRefreshRender&&state.view==='library'&&
       !(active?.closest('#page-content')&&['INPUT','TEXTAREA','SELECT'].includes(active.tagName)))render();
  },120);
});
$('#page-content').addEventListener('change',(event)=>{if(event.target.id==='person-filter'){state.filterPerson=event.target.value;renderLibrary();}if(event.target.id==='sort-select'){state.sort=event.target.value;renderLibrary();}if(event.target.id==='orientation-filter'){state.filterOrientation=event.target.value;renderLibrary();}});
$('#page-content').addEventListener('keydown',(event)=>{if(event.target.id==='chat-input'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();event.target.form.requestSubmit();return;}const card=event.target.closest('.photo-card');if(card&&(event.key==='Enter'||event.key===' ')){if(event.target===card||event.target.classList.contains('photo-frame')){event.preventDefault();const image=card.querySelector('img[data-photo-id]');if(image){if(state.view==='compare')togglePair(image.dataset.photoId);else renderPlayer(image.dataset.photoId);}}}});

const originalUpdateSidebar=updateSidebar;
updateSidebar=function(){originalUpdateSidebar();$('#new-family').style.display=state.user==='local-owner'?'block':'none';$('#share-family').style.display='block';};

$('#page-content').addEventListener('click',event=>{
  if(event.target.closest('[data-action],button,input,label'))return;
  const card=event.target.closest('.photo-card');if(!card)return;
  event.stopPropagation();
  if(state.view==='compare')togglePair(card.dataset.photoId);else renderPlayer(card.dataset.photoId);
});

async function initialize(){
  const input=$('#file-input');input.addEventListener('change',(event)=>uploadFiles(event.target.files,$('[data-action="upload-files"]')||$('.upload-cta')));
  document.body.addEventListener('dragover',(event)=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();document.body.classList.add('dragging');}});
  document.body.addEventListener('dragleave',(event)=>{if(!event.relatedTarget)document.body.classList.remove('dragging');});
  document.body.addEventListener('drop',(event)=>{if(!event.dataTransfer?.files.length)return;event.preventDefault();document.body.classList.remove('dragging');uploadFiles(event.dataTransfer.files,$('.upload-cta'));});
  for(const dialog of $$('dialog'))dialog.addEventListener('click',(event)=>{if(event.target===dialog)dialog.close();});
  try { const health=await api('/healthz',{auth:false});state.mode=health.mode;state.chatToolsAvailable=health.chatToolsAvailable===true; }
  catch(error) { state.mode='unknown';notify(`Could not check the connection. ${error.message}`,'error'); }

  render();
  if(state.mode==='aws' && state.token==='local-development-only')showAccess();
  if(state.family){
    await refreshLibrary({quiet:true});
    if(restoreUiSnapshot?.albumPhotos&&Array.isArray(restoreUiSnapshot.albumPhotos.photoIds)){
      state.albumPhotos={title:restoreUiSnapshot.albumPhotos.title||'Matching photos',albumId:restoreUiSnapshot.albumPhotos.albumId||null,
        photos:await photosByIds(restoreUiSnapshot.albumPhotos.photoIds)};
      render();
    }
  }
  else{$('#connection-panel').classList.remove('hidden');$('#connection-panel').innerHTML='<span>Create or select a family album to start adding photos. Your account must have permission to upload.</span><button data-action="new-family">Get started</button>';}
}
initialize();
