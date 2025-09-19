// Use ES modules from CDN (pin to r160 to match API)
import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js?module';
import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js?module';

/* ==========
   Parameters
   ========== */
const CUBE_SIZE = 1;                 // 1 unit = 1 cm
const COLS = 6;                      // x: 1..6
const ROWS = 10;                     // y: 1..10
const FRONT_NORMAL = new THREE.Vector3(0, 0, 1);  // +Z front
const RIGHT_NORMAL = new THREE.Vector3(1, 0, 0);  // +X right
const ANG_TOL = 0.9;                 // cosθ threshold for face picking
const BLUE = 0x3b82f6;

const INITIAL_VIEW = {
  position: { x: 14.498380, y: 7.191459, z: 17.062261 },
  target:   { x: 0.329985,  y: 3.830037, z: -0.117474 },
  fov: 55.000,
  zoom: 1
};

// Polling config
const AGV_POLL_RATE_MS = 500;
let agvPollingIntervalId = null;
let currentAgvTaskNumber = null;

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

// Controls: L-rotate, R-pan, wheel-zoom
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

// Grid (optional)
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

// Left-top status (existing). If your HTML already has #status, we reuse it.
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

// Top-right buttons container
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

// Simple button factory
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
const btnStat  = mkBtn('AGV STATUS');

uiBox.appendChild(btnHome);
uiBox.appendChild(btnStat);

// Right-top plain text panel (directly below AGV STATUS button)
const statPanel = ensureDom('agv-status-panel', 'pre', {
  position: 'fixed',
  top: '90px',            // 10px + button heights (~48px) + gap
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
statPanel.textContent = ''; // hidden if empty
statPanel.style.display = 'none';

/* ==========================
   Geometries / Hover helpers
   ========================== */
const boxGeom = new THREE.BoxGeometry(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE);
const invisibleMat = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.0 });
const edgeMat = new THREE.LineBasicMaterial({ color: BLUE });

// Hover plane
const hoverPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(CUBE_SIZE, CUBE_SIZE),
  new THREE.MeshBasicMaterial({ color: 0xff2d2e, transparent: true, opacity: 0.6, side: THREE.DoubleSide, depthTest:false, depthWrite:false })
);
hoverPlane.renderOrder = 998;
hoverPlane.visible = false;
scene.add(hoverPlane);

// Build 6x10 cubes (hidden solids for raycast + visible edges)
function xPosFromIndex(ix) { return (ix - (COLS + 1) / 2) * CUBE_SIZE; }
function yPosFromIndex(iy) { return (iy - 0.5) * CUBE_SIZE; }
const zPos = 0;

const pickMeshes = [];
for (let ix = 1; ix <= COLS; ix++) {
  for (let iy = 1; iy <= ROWS; iy++) {
    const mesh = new THREE.Mesh(boxGeom, invisibleMat);
    mesh.position.set(xPosFromIndex(ix), yPosFromIndex(iy), zPos);
    mesh.userData = { x: ix, y: iy };
    scene.add(mesh);
    pickMeshes.push(mesh);

    const edges = new THREE.EdgesGeometry(boxGeom, 1);
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
let isAgvBusy = false;

function updateAgvBusyStatus(busy, message = "", agvRawStatus = null) {
  isAgvBusy = busy;
  if (isAgvBusy) {
    statusEl.innerHTML = `<span class="err" style="color:#fca5a5">AGV 忙碌中：${message || '上一個工作尚未完成'}，請稍候。</span>`;
  } else {
    if (statusEl.innerText.trim() === '' || statusEl.innerHTML.includes("AGV 忙碌中")) {
      statusEl.textContent = `AGV 就緒，可點擊方塊送出命令。`;
    }
    if (agvRawStatus && (agvRawStatus.status === "WAITING" || agvRawStatus.status === "CHARGING" || agvRawStatus.status === "IDLE")) {
      stopPollingAgvStatus();
    }
  }
}

async function pollAgvStatus() {
  try {
    const resp = await fetch("/agv/status-summary");
    const data = await resp.json();

    if (resp.ok && data.agv_busy !== undefined) {
      if (!data.agv_busy) {
        updateAgvBusyStatus(false, "AGV 就緒", data.agv_raw_status);
        stopPollingAgvStatus();
        return;
      }
      updateAgvBusyStatus(data.agv_busy, data.agv_status_message, data.agv_raw_status);

      // task tracking end conditions
      if (currentAgvTaskNumber && data.agv_raw_status?.task?.taskNumber === currentAgvTaskNumber) {
        const taskStatus = data.agv_raw_status.task.status;
        if (taskStatus === "COMPLETED" || taskStatus === "FAILED" || taskStatus === "CANCELLED") {
          stopPollingAgvStatus();
        }
      } else if (currentAgvTaskNumber && !data.agv_raw_status?.task) {
        stopPollingAgvStatus();
      }
    } else {
      console.error("Failed to fetch AGV status summary:", data);
      updateAgvBusyStatus(true, data.error || "Server 無法取得 AGV 狀態。");
    }
  } catch (error) {
    console.error("Error polling AGV status:", error);
    updateAgvBusyStatus(true, `通訊錯誤：${error.message}`);
  }
}

function startPollingAgvStatus(taskNumber = null) {
  if (agvPollingIntervalId === null) {
    agvPollingIntervalId = setInterval(pollAgvStatus, AGV_POLL_RATE_MS);
    currentAgvTaskNumber = taskNumber;
  }
}

function stopPollingAgvStatus() {
  if (agvPollingIntervalId !== null) {
    clearInterval(agvPollingIntervalId);
    agvPollingIntervalId = null;
    currentAgvTaskNumber = null;
  }
}

/* ===============
   AGV interactions
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
  if (isAgvBusy) {
    console.log("AGV busy; blocked new command.");
    return;
  }
  statusEl.textContent = `送出：x=${x}, y=${y} ...`;
  updateAgvBusyStatus(true, "送出中...");

  fetch("/click", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y })
  })
  .then(async (resp) => {
    let d;
    try { d = await resp.json(); }
    catch (e) { throw new Error(`Invalid JSON response: ${e}`); }

    if (d.agv_result?.busy) {
      updateAgvBusyStatus(true, d.agv_result.error || `AGV 忙碌, 狀態: ${d.agv_result.status}`);
      stopPollingAgvStatus();
      return;
    }

    if (!d.ok) {
      statusEl.innerHTML = `<span class="err" style="color:#fca5a5">Error: ${d.error || d.agv_result?.error || 'unknown'}</span>`;
      updateAgvBusyStatus(true, d.error || d.agv_result?.error || 'unknown');
      stopPollingAgvStatus();
      return;
    }

    if (d.agv_result?.noop) {
      statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">AGV 已在目標點（${d.agv_result.target}）</span>；確認軌道...`;
      updateAgvBusyStatus(true, "AGV no-op，等待軌道完成...");
      startPollingAgvStatus(null);
      return;
    }

    statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">OK</span> （伺服器回覆 x=${d.x}, y=${d.y}）`;
    updateAgvBusyStatus(true, "AGV 任務已送出，等待完成...");
    startPollingAgvStatus(d.agv_result ? d.agv_result.taskNumber : null);
  })
  .catch(err => {
    statusEl.innerHTML = `狀態：<span class="err" style="color:#fca5a5">Request failed: ${err.message || err}</span>`;
    updateAgvBusyStatus(true, `Request failed: ${err.message || err}`);
    stopPollingAgvStatus();
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
  if (isAgvBusy) return;
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
  if (isAgvBusy) { hideHover(); return; }
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
  if (isAgvBusy) { console.log("AGV busy; click ignored."); return; }
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);

  // Only scene picking (UI is DOM, so no 3D UI checks needed)
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
  if (isAgvBusy) return;
  clearRightTopStatus();
  statusEl.textContent = `送出：AGV HOME (1001) ...`;
  updateAgvBusyStatus(true, "Sending AGV HOME...");
  fetch('/agv/send-task', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ target: '1001' })
  })
  .then(r => r.json().then(d => ({ok:r.ok, d})))
  .then(({ok, d}) => {
    if (!ok || !d.ok) {
      const msg = d.error || d.message || 'AGV HOME failed';
      if (String(msg).includes('already the target')) {
        statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">AGV 已在 HOME (1001)</span>；確認軌道...`;
        updateAgvBusyStatus(true, "AGV no-op；等待軌道完成...");
        startPollingAgvStatus(null);
        return;
      }
      statusEl.innerHTML = `<span class="err" style="color:#fca5a5">Error: ${msg}</span>`;
      updateAgvBusyStatus(true, msg);
      stopPollingAgvStatus();
      return;
    }
    if (d.noop || (d.message && String(d.message).includes('already the target'))) {
      statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">AGV 已在 HOME (1001)</span>；確認軌道...`;
      updateAgvBusyStatus(true, "AGV no-op；等待軌道完成...");
      startPollingAgvStatus(null);
      return;
    }
    statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">AGV HOME 已送出</span>`;
    updateAgvBusyStatus(true, "AGV HOME 已啟動，等待完成...");
    startPollingAgvStatus(d.taskNumber || null);
  })
  .catch(err => {
    statusEl.innerHTML = `狀態：<span class="err" style="color:#fca5a5">Request failed: ${err.message || err}</span>`;
    updateAgvBusyStatus(true, `Request failed: ${err.message || err}`);
    stopPollingAgvStatus();
  });
});

btnStat.addEventListener('click', () => {
  fetch('/agv/status-summary')
    .then(r => r.json().then(d => ({ok:r.ok, d})))
    .then(({ok, d}) => {
      renderRightTopStatus(d, !ok);
    })
    .catch(err => {
      renderRightTopStatus(String(err), true);
    });
});

/* =========
   Animate
   ========= */
function animate() {
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
animate();

/* =========
   Resize
   ========= */
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  // DOM UI auto-follows via fixed positioning; nothing else to do
});

/* ==========================
   Initial one-shot status check
   ========================== */
window.onload = () => {
  console.log("Initial AGV status check on page load.");
  pollAgvStatus();
  // Initial hint
  if (!statusEl.textContent.trim()) {
    statusEl.textContent = 'AGV 狀態讀取中...';
  }
};
