const form = document.querySelector('#trip-form');
const results = document.querySelector('#results');
const loading = document.querySelector('#loading');
const formError = document.querySelector('#form-error');
const button = document.querySelector('#generate-button');
let activeRequest = null;
let itinerary = null;
let activeTripRequest = null;
let conversationId = null;
let conversationRevision = null;
let activeChatRequest = null;

function setFieldError(id, message = '') { const field = document.querySelector(`#${id}`).closest('.field'); field.classList.toggle('invalid', Boolean(message)); field.querySelector('small').textContent = message; }
function selectedInterests() { return [...document.querySelectorAll('#interests input:checked')].map(input => input.value); }
function validate() {
  let valid = true; formError.hidden = true;
  const destination = document.querySelector('#destination').value.trim(); const start = document.querySelector('#start-date').value; const end = document.querySelector('#end-date').value;
  const budget = document.querySelector('#budget').value; const currency = document.querySelector('#currency').value.trim(); const travelers = Number(document.querySelector('#travelers').value);
  setFieldError('destination', destination ? '' : 'Enter a destination.'); valid &&= Boolean(destination);
  setFieldError('start-date', start ? '' : 'Choose a start date.'); valid &&= Boolean(start);
  setFieldError('end-date', end ? (end >= start ? '' : 'End date must be on or after start date.') : 'Choose an end date.'); valid &&= Boolean(end && end >= start);
  setFieldError('budget', budget && Number(budget) < 0 ? 'Budget cannot be negative.' : ''); valid &&= !(budget && Number(budget) < 0);
  setFieldError('currency', budget && !/^[a-zA-Z]{3}$/.test(currency) ? 'Use a three-letter currency code.' : ''); valid &&= !(budget && !/^[a-zA-Z]{3}$/.test(currency));
  setFieldError('travelers', travelers >= 1 && travelers <= 20 ? '' : 'Enter between 1 and 20 travelers.'); valid &&= travelers >= 1 && travelers <= 20;
  return valid;
}
function payload() {
  const budget = document.querySelector('#budget').value;
  const dietary = document.querySelector('#dietary').value.split(',').map(v => v.trim()).filter(Boolean);
  return { destination: document.querySelector('#destination').value.trim(), start_date: document.querySelector('#start-date').value, end_date: document.querySelector('#end-date').value, budget: budget ? Number(budget) : null, currency: document.querySelector('#currency').value.trim().toUpperCase() || null, travelers: Number(document.querySelector('#travelers').value), interests: selectedInterests(), travel_style: document.querySelector('#travel-style').value, pace: document.querySelector('#pace').value, accommodation_preference: document.querySelector('#accommodation').value.trim() || null, dietary_preferences: dietary, additional_constraints: document.querySelector('#constraints').value.trim() || null };
}
function showLoading(title, copy) { loading.hidden = false; results.hidden = true; document.querySelector('#loading-title').textContent = title; document.querySelector('#loading-copy').textContent = copy; button.disabled = true; }
function addNotice(message, kind = 'warning') { const node = document.createElement('div'); node.className = `notice ${kind}`; node.textContent = message; document.querySelector('#notices').append(node); }
function parseSseChunk(chunk, onEvent) { for (const block of chunk.split('\n\n')) { if (!block.trim()) continue; const lines = block.split('\n'); const event = lines.find(line => line.startsWith('event: '))?.slice(7); const data = lines.find(line => line.startsWith('data: '))?.slice(6); if (event && data) { try { onEvent(event, JSON.parse(data)); } catch { addNotice('Received an unreadable server update.', 'error'); } } } }
async function generate(data) {
  activeTripRequest = data; resetChat();
  if (activeRequest) activeRequest.abort(); activeRequest = new AbortController();
  showLoading('Preparing your trip details', 'Checking your preferences and destination.'); document.querySelector('#notices').replaceChildren();
  try {
    const response = await fetch('/api/v1/itineraries/generate/stream', { method: 'POST', headers: {'Content-Type':'application/json', 'Accept':'text/event-stream'}, body: JSON.stringify(data), signal: activeRequest.signal });
    if (!response.ok || !response.body) throw new Error(`The service could not start (${response.status}).`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffered = '';
    const handle = (event, data) => {
      if (event === 'status') showLoading('Fetching destination information', 'Collecting available weather and place data.');
      if (event === 'context_ready') { showLoading('Generating your itinerary', data.place_count ? `Using ${data.place_count} verified places where relevant.` : 'Working with the available destination context.'); data.limitations?.forEach(message => addNotice(message)); }
      if (event === 'warning') addNotice(data.message);
      if (event === 'error') throw new Error(data.message || 'Itinerary generation failed.');
      if (event === 'itinerary') { itinerary = data; renderResults(data); }
    };
    while (true) { const {value, done} = await reader.read(); buffered += decoder.decode(value || new Uint8Array(), {stream: !done}); const parts = buffered.split('\n\n'); buffered = parts.pop(); parts.forEach(part => parseSseChunk(`${part}\n\n`, handle)); if (done) break; }
  } catch (error) { if (error.name !== 'AbortError') { formError.textContent = error.message || 'Unable to generate an itinerary. Please try again.'; formError.hidden = false; document.querySelector('#planner').scrollIntoView({behavior:'smooth'}); } }
  finally { loading.hidden = true; button.disabled = false; activeRequest = null; }
}
function renderResults(data, scroll = true) {
  document.querySelector('#result-destination').textContent = data.destination;
  document.querySelector('#result-dates').textContent = `${data.start_date} — ${data.end_date}`;
  document.querySelector('#result-meta').textContent = `${data.number_of_days} ${data.number_of_days === 1 ? 'day' : 'days'} · ${data.travelers} ${data.travelers === 1 ? 'traveler' : 'travelers'}${data.budget ? ` · ${data.currency} ${data.budget}` : ''}`;
  document.querySelector('#result-summary').textContent = data.summary;
  [...(data.limitations || []), ...(data.validation_warnings || [])].forEach(message => addNotice(message));
  renderDays(data.days);
  renderAccommodation(data.accommodation);
  renderDining(data.dining);
  renderBudget(data.budget_estimate);
  document.querySelector('#tips').replaceChildren(...(data.practical_tips || data.assumptions || []).map(text => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  }));
  renderChat();
  results.hidden = false;
  if (scroll) results.scrollIntoView({behavior:'smooth'});
}

function renderAccommodation(accommodation) {
  const target = document.querySelector('#accommodation-content');
  if (!target) return;
  target.replaceChildren();
  if (!accommodation) {
    target.innerHTML = '<p class="muted-note">No specific accommodation records retrieved.</p>';
    return;
  }
  const wrap = document.createElement('div');
  wrap.className = 'acc-details';
  wrap.innerHTML = `<p class="acc-pref"><strong>Preference:</strong> ${escapeHtml(accommodation.preference || 'Standard lodging')}</p><p class="acc-area"><strong>Area:</strong> ${escapeHtml(accommodation.recommended_area || 'Central')}</p>`;
  if (accommodation.hotel_options && accommodation.hotel_options.length > 0) {
    const hotelList = document.createElement('div');
    hotelList.className = 'spot-list';
    accommodation.hotel_options.slice(0, 4).forEach(h => {
      const card = document.createElement('div');
      card.className = 'spot-card';
      card.innerHTML = `<strong>${escapeHtml(h.name)}</strong>${h.address ? `<small>${escapeHtml(h.address)}</small>` : ''}`;
      hotelList.append(card);
    });
    wrap.append(hotelList);
  }
  if (accommodation.practical_tips && accommodation.practical_tips.length > 0) {
    const tipsList = document.createElement('ul');
    tipsList.className = 'detail-tips';
    accommodation.practical_tips.slice(0, 2).forEach(tip => {
      const li = document.createElement('li');
      li.textContent = tip;
      tipsList.append(li);
    });
    wrap.append(tipsList);
  }
  target.append(wrap);
}

function renderDining(dining) {
  const target = document.querySelector('#dining-content');
  if (!target) return;
  target.replaceChildren();
  if (!dining) {
    target.innerHTML = '<p class="muted-note">No specific dining records retrieved.</p>';
    return;
  }
  const wrap = document.createElement('div');
  wrap.className = 'dining-details';
  if (dining.dietary_preferences && dining.dietary_preferences.length > 0) {
    wrap.innerHTML += `<p class="dining-diet"><strong>Dietary:</strong> ${escapeHtml(dining.dietary_preferences.join(', '))}</p>`;
  }
  if (dining.recommended_spots && dining.recommended_spots.length > 0) {
    const spotList = document.createElement('div');
    spotList.className = 'spot-list';
    dining.recommended_spots.slice(0, 4).forEach(s => {
      const card = document.createElement('div');
      card.className = 'spot-card';
      card.innerHTML = `<strong>${escapeHtml(s.name)}</strong>${s.address ? `<small>${escapeHtml(s.address)}</small>` : ''}`;
      spotList.append(card);
    });
    wrap.append(spotList);
  }
  if (dining.dining_tips && dining.dining_tips.length > 0) {
    const tipsList = document.createElement('ul');
    tipsList.className = 'detail-tips';
    dining.dining_tips.slice(0, 2).forEach(tip => {
      const li = document.createElement('li');
      li.textContent = tip;
      tipsList.append(li);
    });
    wrap.append(tipsList);
  }
  target.append(wrap);
}
function renderDays(days) { const tabs = document.querySelector('#day-tabs'); tabs.replaceChildren(); days.forEach((day, index) => { const tab = document.createElement('button'); tab.type='button'; tab.role='tab'; tab.textContent = `Day ${day.day_number}`; tab.setAttribute('aria-selected', index === 0); tab.onclick = () => { [...tabs.children].forEach(item => item.setAttribute('aria-selected','false')); tab.setAttribute('aria-selected','true'); renderDay(day); }; tabs.append(tab); }); renderDay(days[0]); }
function renderDay(day) { const panel = document.querySelector('#day-content'); panel.replaceChildren(); const content = document.createElement('div'); content.className='day-panel'; content.innerHTML = `<h3>Day ${day.day_number}: ${escapeHtml(day.theme)}</h3>${day.weather_note ? `<p class="weather-note">◌ ${escapeHtml(day.weather_note)}</p>` : ''}`; day.activities.forEach(activity => { const item = document.createElement('article'); item.className='activity'; const verified = activity.source === 'geoapify'; item.innerHTML = `<div class="activity-time">${escapeHtml(activity.time_of_day)}</div><div><h4>${escapeHtml(activity.title)}</h4><p>${escapeHtml(activity.description)}</p><div class="activity-meta"><span class="badge ${verified ? '' : 'general'}">${verified ? 'Verified place' : 'General suggestion'}</span><span class="badge">${activity.duration_minutes} min</span>${activity.place_name ? `<span class="badge">${escapeHtml(activity.place_name)}</span>` : ''}</div></div>`; content.append(item); }); panel.append(content); }
function renderBudget(budget) {
  const target = document.querySelector('#budget-content');
  target.replaceChildren();
  const lines = [];
  if (budget?.stated_budget != null) {
    const curr = budget.currency || '';
    lines.push(['Trip budget', `${curr} ${budget.stated_budget}`]);
    lines.push(['Per day', `${curr} ${budget.per_day_budget}`]);
    lines.push(['Per traveler', `${curr} ${budget.per_person_budget}`]);
    if (budget.accommodation_estimate != null) {
      lines.push(['Stay allocation (est)', `${curr} ${budget.accommodation_estimate}`]);
    }
    if (budget.food_estimate != null) {
      lines.push(['Food allocation (est)', `${curr} ${budget.food_estimate}`]);
    }
    if (budget.transport_estimate != null) {
      lines.push(['Transit allocation (est)', `${curr} ${budget.transport_estimate}`]);
    }
    if (budget.activities_estimate != null) {
      lines.push(['Activities allocation (est)', `${curr} ${budget.activities_estimate}`]);
    }
  } else {
    lines.push(['Budget', 'Not provided']);
  }
  lines.forEach(([label,value]) => {
    const line=document.createElement('div');
    line.className='budget-line';
    line.innerHTML=`<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    target.append(line);
  });
}
function escapeHtml(value) { const node=document.createElement('div'); node.textContent=value ?? ''; return node.innerHTML; }
function resetChat() {
  conversationId = null; conversationRevision = null;
  const panel = document.querySelector('#chat-panel');
  if (panel) panel.hidden = true;
  document.querySelector('#chat-messages')?.replaceChildren();
  document.querySelector('#chat-error').hidden = true;
}
function renderChat() {
  const panel = document.querySelector('#chat-panel'); const isNew = panel.hidden; panel.hidden = false;
  const context = document.querySelector('#chat-context'); context.replaceChildren();
  [[`📍 ${itinerary.destination}`], [`◷ ${itinerary.number_of_days} ${itinerary.number_of_days === 1 ? 'day' : 'days'}`], itinerary.budget ? [`◉ ${itinerary.currency} ${itinerary.budget}`] : null].filter(Boolean).forEach(([label]) => { const chip = document.createElement('span'); chip.textContent = label; context.append(chip); });
  renderSuggestions();
  if (isNew) { document.querySelector('#chat-messages').replaceChildren(); addChatMessage('assistant', `I’m ready to help with your ${itinerary.destination} plan. Ask me to explain a day, check the stated budget, or make a specific adjustment.`); }
}
function renderSuggestions() {
  const target = document.querySelector('#chat-suggestions'); target.replaceChildren();
  const options = [`Explain Day 1`, itinerary.number_of_days > 1 ? 'Make Day 2 more relaxed' : 'Make Day 1 more relaxed', itinerary.budget ? 'Will this fit my budget?' : 'Recalculate my budget'];
  options.forEach(text => { const suggestion = document.createElement('button'); suggestion.type = 'button'; suggestion.textContent = text; target.append(suggestion); });
}
function addChatMessage(role, text, changes = []) {
  const messages = document.querySelector('#chat-messages'); const item = document.createElement('article'); item.className = `message ${role}`;
  const label = document.createElement('span'); label.className = 'message-label'; label.textContent = role === 'assistant' ? 'TRIPPILOT' : 'YOU';
  const body = document.createElement('p'); body.textContent = text; item.append(label, body);
  if (changes.length) { const change = document.createElement('span'); change.className = 'change'; change.textContent = changes.join(' · '); item.append(change); }
  messages.append(item); messages.scrollTop = messages.scrollHeight;
}
function setChatPending(pending) {
  document.querySelector('#chat-typing').hidden = !pending;
  document.querySelector('#chat-messages').setAttribute('aria-busy', String(pending));
  document.querySelector('#chat-form button').disabled = pending;
  document.querySelectorAll('#chat-suggestions button').forEach(button => { button.disabled = pending; });
}
async function sendChat(message) {
  if (!message.trim() || !itinerary || !activeTripRequest || activeChatRequest) return;
  const input = document.querySelector('#chat-input'); const error = document.querySelector('#chat-error');
  error.hidden = true; addChatMessage('user', message); input.value = ''; setChatPending(true); document.querySelector('#chat-status').innerHTML = '<i></i>Thinking';
  const body = { message, conversation_id: conversationId, revision: conversationRevision, itinerary: conversationId ? null : itinerary, trip_request: conversationId ? null : activeTripRequest };
  activeChatRequest = new AbortController();
  try {
    const response = await fetch('/api/v1/conversations/messages', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body), signal: activeChatRequest.signal });
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'The assistant could not process that request.');
    conversationId = data.conversation_id; conversationRevision = data.revision; addChatMessage('assistant', data.assistant_reply, data.change_summary || []);
    if (data.modified && data.itinerary) { itinerary = data.itinerary; renderResults(itinerary, false); document.querySelector('#chat-status').innerHTML = `<i></i>Updated · r${conversationRevision}`; }
    else document.querySelector('#chat-status').innerHTML = `<i></i>Ready · r${conversationRevision}`;
  } catch (err) { if (err.name !== 'AbortError') { error.textContent = err.message || 'Unable to reach the assistant.'; error.hidden = false; } }
  finally { setChatPending(false); activeChatRequest = null; }
}
document.querySelector('#chat-form').addEventListener('submit', event => { event.preventDefault(); sendChat(document.querySelector('#chat-input').value); });
document.querySelector('#chat-suggestions').addEventListener('click', event => { if (event.target.matches('button')) sendChat(event.target.textContent); });
document.querySelector('#new-chat').addEventListener('click', () => { resetChat(); renderChat(); });
form.addEventListener('submit', event => { event.preventDefault(); if (validate()) generate(payload()); });
document.querySelector('#edit-trip').addEventListener('click', () => document.querySelector('#planner').scrollIntoView({behavior:'smooth'}));
