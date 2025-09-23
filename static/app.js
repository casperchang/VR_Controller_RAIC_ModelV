// Use ES modules from CDN (pin to r160 to match API)
import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js?module';
import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js?module';

/* ==========
   Parameters
   ========== */
const CUBE_SIZE = 1;
const COLS = 6;
const ROWS = 10;
const FRONT_NORMAL = new THREE.Vector3(0, 0, 1);
const RIGHT_NORMAL = new THREE.Vector3(1, 0, 0);
const ANG_TOL = 0.9;
const BLUE = 0x3b82f6;

const INITIAL_VIEW = {
  position: { x: 14.498380, y: 7.191459, z: 17.062261 },
  target:   { x: 0.329985,  y: 3.830037, z: -0.117474 },
  fov: 55.000,
  zoom: 1
};

// Polling config
const SYSTEM_POLL_RATE_MS = 500;
let systemPollingIntervalId = null;

/* =====
   Scene
   ===== */
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0e13);

const camera = new THREE.PerspectiveCamera(
  INITIAL_VIEW.fov,
  window.innerWidth / window.innerHeight,
  0.1, 1000
);
camera.position.set(INITIAL_VIEW.position.x, INITIAL_VIEW.position.y, INITIAL_VIEW.position.z);
camera.zoom = INITIAL_VIEW.zoom;
camera.updateProjectionMatrix();

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

// Controls
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.mouseButtons = {
  LEFT: THREE.MOUSE.ROTATE,
  MIDDLE: THREE.MOUSE.DOLLY,
  RIGHT: THREE.MOUSE.PAN
};
controls.minDistance = 3;
controls.maxDistance = 30;
controls.target.set(INITIAL_VIEW.target.x, INITIAL_VIEW.target.y, INITIAL_VIEW.target.z);
controls.update();

// Lights
scene.add(new THREE.HemisphereLight(0x8899aa, 0x111111, 0.7));
const dir = new THREE.DirectionalLight(0xffffff, 0.4);
dir.position.set(3,5,6);
scene.add(dir);

// Grid
const grid = new THREE.GridHelper(40, 40, 0x334155, 0x1f2937);
grid.position.y = -0.51;
scene.add(grid);

/* ==========
   UI (DOM)
   ========== */
function ensureDom(elId, tag, styles = {}) {
  let el = document.getElementById(elId);
  if (!el) {
    el = document.createElement(tag);
    el.id = elId;
    document.body.appendChild(el);
  }
  Object.assign(el.style, styles);
  return el;
}

const statusEl = ensureDom('status', 'div', {
  position: 'fixed',
  top: '80px',
  left: '10px',
  zIndex: 1000,
  color: '#e5e7eb',
  fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Helvetica, Arial, sans-serif',
  fontSize: '14px',
  lineHeight: '1.3',
  background: 'rgba(0,0,0,0.35)',
  padding: '8px 10px',
  borderRadius: '8px',
  border: '1px solid rgba(59,130,246,.35)',
  maxWidth: '32vw',
  whiteSpace: 'pre-wrap'
});

const uiBox = ensureDom('ui-box', 'div', {
  position: 'fixed',
  top: '10px',
  right: '10px',
  zIndex: 1000,
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  alignItems: 'flex-end'
});

function mkBtn(text) {
  const b = document.createElement('button');
  b.textContent = text;
  Object.assign(b.style, {
    appearance: 'none',
    border: '1px solid #3b82f6',
    background: 'rgba(11,14,19,0.35)',
    color: '#e5e7eb',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '14px',
    cursor: 'pointer'
  });
  b.onmouseenter = () => (b.style.background = 'rgba(59,130,246,.15)');
  b.onmouseleave = () => (b.style.background = 'rgba(11,14,19,0.35)');
  return b;
}

const btnHome  = mkBtn('AGV HOME');
const btnStat  = mkBtn('SYSTEM STATUS');
uiBox.appendChild(btnHome);
uiBox.appendChild(btnStat);

const statPanel = ensureDom('agv-status-panel', 'pre', {
  position: 'fixed',
  top: '90px',
  right: '10px',
  zIndex: 1000,
  color: '#e5e7eb',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
  fontSize: '12px',
  lineHeight: '1.35',
  background: 'rgba(0,0,0,0.3)',
  padding: '10px 12px',
  borderRadius: '8px',
  border: '1px solid rgba(59,130,246,.25)',
  maxWidth: '36vw',
  maxHeight: '44vh',
  overflow: 'auto',
  margin: 0,
  whiteSpace: 'pre-wrap'
});
statPanel.textContent = '';
statPanel.style.display = 'none';

/* ==========================
   Geometries / Hover helpers
   ========================== */
const CUBE = new THREE.BoxGeometry(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE);
const invisibleMat = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.0 });
const edgeMat = new THREE.LineBasicMaterial({ color: BLUE });

const hoverPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(CUBE_SIZE, CUBE_SIZE),
  new THREE.MeshBasicMaterial({ color: 0xff2d2e, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthTest:false, depthWrite:false })
);
hoverPlane.renderOrder = 998;
hoverPlane.visible = false;
scene.add(hoverPlane);

function xPosFromIndex(ix) { return (ix - (COLS + 1) / 2) * CUBE_SIZE; }
function yPosFromIndex(iy) { return (iy - 0.5) * CUBE_SIZE; }
const zPos = 0;

const pickMeshes = [];
for (let ix = 1; ix <= COLS; ix++) {
  for (let iy = 1; iy <= ROWS; iy++) {
    const mesh = new THREE.Mesh(CUBE, invisibleMat);
    mesh.position.set(xPosFromIndex(ix), yPosFromIndex(iy), zPos);
    mesh.userData = { x: ix, y: iy };
    scene.add(mesh);
    pickMeshes.push(mesh);

    const edges = new THREE.EdgesGeometry(CUBE, 1);
    const line = new THREE.LineSegments(edges, edgeMat);
    line.position.copy(mesh.position);
    scene.add(line);
  }
}

// Raycaster
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

/* ======================
   Busy status & Polling
   ====================== */
let isSystemBusy = false;

function updateSystemBusyStatus(busy, message = "") {
  isSystemBusy = busy;
  if (isSystemBusy) {
    statusEl.innerHTML = `<span class="err" style="color:#fca5a5">系統忙碌中：${message || '上一個工作尚未完成'}，請稍候。</span>`;
    hideHover();
  } else {
    // Only update the message if the state is changing from busy to not-busy
    if (statusEl.innerHTML.includes("系統忙碌中") || statusEl.innerHTML.includes("Error")) {
         statusEl.innerHTML = `<span class="ok" style="color:#86efac">系統就緒：${message || '可點擊方塊送出新命令。'}</span>`;
    }
  }
}

async function pollSystemStatus() {
  try {
    const resp = await fetch("/agv/status-summary");
    const data = await resp.json();
    if (resp.ok && data.system_busy !== undefined) {
      if (data.system_busy) {
        updateSystemBusyStatus(true, data.details?.message || "執行中...");
      } else {
        updateSystemBusyStatus(false, "任務完成");
        stopPollingSystemStatus();
      }
    } else {
      console.error("Failed to fetch system status summary:", data);
      updateSystemBusyStatus(true, data.error || "Server 無法取得系統狀態。");
    }
  } catch (error) {
    console.error("Error polling system status:", error);
    updateSystemBusyStatus(true, `通訊錯誤：${error.message}`);
  }
}

function startPollingSystemStatus() {
  if (systemPollingIntervalId === null) {
    pollSystemStatus(); // Poll immediately
    systemPollingIntervalId = setInterval(pollSystemStatus, SYSTEM_POLL_RATE_MS);
  }
}

function stopPollingSystemStatus() {
  if (systemPollingIntervalId !== null) {
    clearInterval(systemPollingIntervalId);
    systemPollingIntervalId = null;
  }
}

/* ===============
   Interactions
   =============== */
function renderRightTopStatus(objOrString, isError = false) {
  statPanel.style.display = 'block';
  if (typeof objOrString === 'string') {
    statPanel.textContent = objOrString;
  } else {
    statPanel.textContent = JSON.stringify(objOrString, null, 2);
  }
  statPanel.style.borderColor = isError ? 'rgba(252,165,165,.6)' : 'rgba(59,130,246,.25)';
}
function clearRightTopStatus() {
  statPanel.textContent = '';
  statPanel.style.display = 'none';
}

function sendClick(x, y) {
  if (isSystemBusy) {
    console.log("System busy; blocked new command.");
    return;
  }
  statusEl.textContent = `送出：x=${x}, y=${y} ...`;
  updateSystemBusyStatus(true, "正在分派任務...");

  fetch("/click", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y })
  })
  .then(async (resp) => {
    const data = await resp.json();
    // If the server rejected the command (e.g., busy), unlock the UI.
    if (!resp.ok || !data.ok) {
        const errorMsg = data.error || '未知的錯誤';
        statusEl.innerHTML = `<span class="err" style="color:#fca5a5">指令失敗: ${errorMsg}</span>`;
        updateSystemBusyStatus(false, "指令被伺服器拒絕，請重試。");
        stopPollingSystemStatus(); // Ensure no polling is running
        return;
    }

    // Command was accepted by the server. Start polling for completion.
    statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">OK</span> (任務已分派 x=${data.x}, y=${data.y})`;
    startPollingSystemStatus();
  })
  .catch(err => {
    statusEl.innerHTML = `狀態：<span class="err" style="color:#fca5a5">請求失敗: ${err.message || err}</span>`;
    updateSystemBusyStatus(false, `請求失敗，請檢查網路連線。`);
    stopPollingSystemStatus();
  });
}

/* ===========
   Hover logic
   =========== */
function setPointerFromEvent(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  const px = (event.clientX - rect.left) / rect.width;
  const py = (event.clientY - rect.top) / rect.height;
  pointer.set(px * 2 - 1, -(py * 2 - 1));
}
const EPS = 0.002;
function showHoverFace(mesh, faceType) {
  if (isSystemBusy) return;
  hoverPlane.visible = true;
  hoverPlane.position.copy(mesh.position);
  hoverPlane.rotation.set(0,0,0);
  if (faceType === 'front') {
    hoverPlane.position.z += CUBE_SIZE/2 + EPS;
  } else if (faceType === 'right') {
    hoverPlane.position.x += CUBE_SIZE/2 + EPS;
    hoverPlane.rotation.y = -Math.PI/2;
  }
}
function hideHover(){ hoverPlane.visible = false; }

/* ================
   Mouse events
   ================ */
function onMouseMove(event) {
  if (isSystemBusy) { hideHover(); return; }
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(pickMeshes, false);
  if (!hits.length) { hideHover(); return; }
  const hit = hits[0];
  const mesh = hit.object;
  const { x } = mesh.userData;
  const worldNormal = hit.face.normal.clone().transformDirection(mesh.matrixWorld);
  if (worldNormal.dot(FRONT_NORMAL) > ANG_TOL) { showHoverFace(mesh, 'front'); return; }
  if (x === COLS && worldNormal.dot(RIGHT_NORMAL) > ANG_TOL) { showHoverFace(mesh, 'right'); return; }
  hideHover();
}
function onClick(event) {
  if (isSystemBusy) { console.log("System busy; click ignored."); return; }
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(pickMeshes, false);
  if (!hits.length) return;
  const hit = hits[0];
  const mesh = hit.object;
  const { x, y } = mesh.userData;
  const worldNormal = hit.face.normal.clone().transformDirection(mesh.matrixWorld);
  if (worldNormal.dot(FRONT_NORMAL) > ANG_TOL) { sendClick(x, y); return; }
  if (x === COLS && worldNormal.dot(RIGHT_NORMAL) > ANG_TOL) { sendClick(7, y); return; }
}
renderer.domElement.addEventListener('mousemove', onMouseMove);
renderer.domElement.addEventListener('click', onClick);

/* ====================
   DOM button handlers
   ==================== */

btnHome.addEventListener('click', () => {
  if (isSystemBusy) return;
  clearRightTopStatus();
  statusEl.textContent = `送出：AGV HOME (1001) ...`;
  updateSystemBusyStatus(true, "正在發送 AGV HOME 指令...");
  fetch('/agv/home', {
    method: 'POST',
    headers: {'Content-Type':'application/json'}
  })
  .then(r => r.json().then(d => ({ok:r.ok, d})))
  .then(({ok, d}) => {
    if (!ok || !d.ok) {
      const msg = d.error || d.message || 'AGV HOME failed';
      statusEl.innerHTML = `<span class="err" style="color:#fca5a5">Error: ${msg}</span>`;
      updateSystemBusyStatus(false, msg);
      stopPollingSystemStatus();
      return;
    }
    statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">已送出 HOME</span>，等待完成...`;
    startPollingSystemStatus();
  })
  .catch(err => {
    statusEl.innerHTML = `狀態：<span class="err" style="color:#fca5a5">請求失敗: ${err.message || err}</span>`;
    updateSystemBusyStatus(false, `請求失敗: ${err.message || err}`);
    stopPollingSystemStatus();
  });
});

btnStat.addEventListener('click', async () => {
  try {
    const r = await fetch('/agv/status-summary');
    const d = await r.json();
    renderRightTopStatus(d, !r.ok || d.ok === false);
  } catch (e) {
    renderRightTopStatus(String(e), true);
  }
});

/* =============
   Render loop
   ============= */
function loop(){
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}
loop();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Initial status check on page load
pollSystemStatus();