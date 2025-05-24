let map, marker;
let lastStreamStates = {};
let lastStreamNames = new Set();

function animateStatusChange(el, newClass) {
    el.classList.remove('bg-success', 'bg-danger', 'bg-secondary', 'status-animate');
    void el.offsetWidth; // reflow for restart animation
    el.classList.add(newClass, 'status-animate');
    setTimeout(() => el.classList.remove('status-animate'), 1000);
}

function showToast(message, type="info") {
    const container = document.getElementById('toast-container');
    const toastId = 'toast-' + Date.now();
    container.innerHTML += `
      <div id="${toastId}" class="toast align-items-center text-bg-${type} border-0 mb-2" role="alert" aria-live="assertive" aria-atomic="true">
        <div class="d-flex">
          <div class="toast-body">${message}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
      </div>`;
    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

function initMap() {
    map = L.map('map').setView([55.75, 37.61], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
    marker = L.marker([55.75, 37.61]).addTo(map);
}
function fetchTelemetry() {
    fetch('/api/telemetry')
      .then(r => r.json())
      .then(data => {
        document.getElementById('telemetry').innerHTML =
          `<b>Телеметрия:</b> Lat: ${data.lat}, Lon: ${data.lon}, Alt: ${data.alt}м, Speed: ${data.speed}км/ч, Heading: ${data.heading}°`;
        if (marker) {
            marker.setLatLng([data.lat, data.lon]);
            map.setView([data.lat, data.lon]);
        }
      });
}
function fetchStreams() {
    fetch('/api/streams')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('streams-list');
            list.innerHTML = '';
            let currentNames = new Set();
            let currentStates = {};
            data.forEach(stream => {
                currentNames.add(stream.path);
                currentStates[stream.path] = stream.publishers > 0 ? 'active' : 'inactive';
                list.innerHTML += `
                <div class="col-md-6 col-lg-4">
                  <div class="card shadow-sm">
                    <div class="card-body">
                      <h5 class="card-title">
                        <i class="fa-solid fa-video"></i>
                        ${stream.path}
                        <span id="status-badge-${stream.path}" class="badge ${stream.publishers > 0 ? 'bg-success' : 'bg-danger'} ms-2">
                          ${stream.publishers > 0 ? 'Активен' : 'Нет сигнала'}
                        </span>
                      </h5>
                      <p class="mb-1">
                        <i class="fa-solid fa-link"></i>
                        <span class="text-monospace">${stream.rtsp_url}</span>
                        <button class="btn btn-sm btn-outline-secondary ms-2" onclick="copyToClipboard('${stream.rtsp_url}')">
                          <i class="fa-regular fa-copy"></i>
                        </button>
                      </p>
                      <p class="mb-1">
                        <i class="fa-solid fa-users"></i> Зрителей: <b>${stream.readers}</b>
                        <span class="ms-3"><i class="fa-regular fa-clock"></i> Аптайм: ${stream.uptime || '-'}</span>
                      </p>
                      <div class="mt-2">
                        <div id="status-alert-${stream.path}" class="alert ${stream.publishers > 0 ? 'alert-success' : 'alert-danger'} p-2 text-center">
                          ${stream.publishers > 0 ? 'Видеопоток активен' : 'Нет сигнала'}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>`;
            });
            // Реактивная анимация для изменения статуса
            data.forEach(stream => {
                const badge = document.getElementById(`status-badge-${stream.path}`);
                const alert = document.getElementById(`status-alert-${stream.path}`);
                const prev = lastStreamStates[stream.path];
                const curr = stream.publishers > 0 ? 'active' : 'inactive';
                if (prev && prev !== curr) {
                    animateStatusChange(badge, curr === 'active' ? 'bg-success' : 'bg-danger');
                    animateStatusChange(alert, curr === 'active' ? 'alert-success' : 'alert-danger');
                }
            });
            lastStreamStates = currentStates;
            // Уведомления о новых/пропавших потоках
            currentNames.forEach(name => {
                if (!lastStreamNames.has(name)) showToast(`Поток ${name} появился`, 'success');
            });
            lastStreamNames.forEach(name => {
                if (!currentNames.has(name)) showToast(`Поток ${name} пропал`, 'danger');
            });
            lastStreamNames = currentNames;
        });
}
function fetchEvents() {
    fetch('/api/events')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('events-list');
            list.innerHTML = '';
            data.forEach(ev => {
                list.innerHTML += `<li class="list-group-item">${ev}</li>`;
            });
        });
}
function copyToClipboard(text) {
    navigator.clipboard.writeText(text);
}
function checkHealth() {
    fetch('/api/health').then(r=>r.json()).then(data=>{
        let el = document.getElementById('health-indicator');
        if (data.status === 'ok') {
            el.className = 'badge bg-success ms-3';
            el.textContent = 'Сервер OK';
        } else {
            el.className = 'badge bg-danger ms-3';
            el.textContent = 'Сервер OFFLINE';
        }
    });
}
window.addEventListener('DOMContentLoaded', () => {
    initMap();
    fetchTelemetry();
    setInterval(fetchTelemetry, 2000);
});
setInterval(fetchStreams, 5000);
setInterval(fetchEvents, 10000);
setInterval(checkHealth, 3000);
fetchStreams();
fetchEvents();
checkHealth(); 