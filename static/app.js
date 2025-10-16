// Use ES modules from CDN (pin to r160 to match API)
// import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js?module';
// import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js?module';
// Use ES modules from local static files
import * as THREE from './js/three.module.js';
import { OrbitControls } from './js/OrbitControls.js';

// ADD THIS LINE to bring TWEEN into the module's scope
const TWEEN = window.TWEEN;
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

const TARGET_TAG_TO_X = {
  '1001': 'home',
  '1002': 1,
  '1003': 2,
  '1004': 3,
  '1005': 4,
  '1006': 5,
  '1007': 6,
  '1009': 7,
};

// const INITIAL_VIEW = {
//   position: { x: 14.498380, y: 7.191459, z: 17.062261 },
//   target:   { x: 0.329985,  y: 3.830037, z: -0.117474 },
//   fov: 55.000,
//   zoom: 1
// };
const INITIAL_VIEW = {
  position: { x: 6.757047, y: 3.650543, z: 21.124597 },
  target:   { x: -5.016409,  y: 4.569626, z: 1.948391 },
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
controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
controls.minDistance = 3;
controls.maxDistance = 30;
controls.target.set(INITIAL_VIEW.target.x, INITIAL_VIEW.target.y, INITIAL_VIEW.target.z);
controls.update();

// START: New code to print camera parameters to the console
// controls.addEventListener('change', () => {
//   const pos = camera.position;
//   const tgt = controls.target;
  
//   console.clear(); // Clears the console to show only the latest values
//   console.log(
// `// Copy these values into your INITIAL_VIEW constant:
// {
//   position: { x: ${pos.x.toFixed(6)}, y: ${pos.y.toFixed(6)}, z: ${pos.z.toFixed(6)} },
//   target:   { x: ${tgt.x.toFixed(6)},  y: ${tgt.y.toFixed(6)}, z: ${tgt.z.toFixed(6)} },
//   fov: ${camera.fov.toFixed(3)},
//   zoom: ${camera.zoom.toFixed(1)}
// }`
//   );
// });
// END: New code

// Lights
scene.add(new THREE.HemisphereLight(0x8899aa, 0x111111, 0.7));
const dir = new THREE.DirectionalLight(0xffffff, 0.4);
dir.position.set(3,5,6);
scene.add(dir);

// Grid
const grid = new THREE.GridHelper(40, 40, 0x334155, 0x1f2937);
grid.position.y = -0.51;
scene.add(grid);

/* =====================
   AGV Indicator Tube
   ===================== */
// Global variable for the new indicator
let agvIndicatorTube = null;
const INDICATOR_HEIGHT = 2 * CUBE_SIZE;

function createAgvIndicator() {
  // 1. Define the geometry for the solid box shape.
  //    (This is the same as before, but we won't convert it to edges).
  const tubeGeom = new THREE.BoxGeometry(CUBE_SIZE, INDICATOR_HEIGHT, CUBE_SIZE);

  // 2. Define the new material for a solid, metallic object.
  const tubeMaterial = new THREE.MeshStandardMaterial({
    color: 0x8b0000,      // Dark red base color
    metalness: 0.7,       // Makes the surface metallic (0=non-metal, 1=fully-metal)
    roughness: 0.3,       // Makes the surface semi-polished (0=mirror, 1=dull)
    transparent: true,    // Enable transparency
    opacity: 0.7,          // Set opacity to 50%
        // --- ADD THESE TWO LINES ---
    emissive: 0xff0000,   // Make it glow with a brighter red
    emissiveIntensity: 0.4 // Adjust this value to control the glow (0 to 1)
  });

  // 3. Create the indicator object as a solid Mesh.
  //    (This is the key change from THREE.LineSegments).
  agvIndicatorTube = new THREE.Mesh(tubeGeom, tubeMaterial);

  // 4. Calculate the initial position (this logic remains the same).
  const initialX = xPosFromIndex(1);
  const initialY = -0.5 + (INDICATOR_HEIGHT / 2);
  const initialZ = CUBE_SIZE*2;

  agvIndicatorTube.position.set(initialX, initialY, initialZ);
  scene.add(agvIndicatorTube);
}

// Call the function to create and add the sphere to the scene
createAgvIndicator();

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
  position: 'fixed', top: '10px', right: '10px', zIndex: 1000,
  display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end'
});

function mkBtn(text) {
  const b = document.createElement('button');
  b.textContent = text;
  Object.assign(b.style, {
    appearance: 'none', border: '1px solid #3b82f6', background: 'rgba(11,14,19,0.35)',
    color: '#e5e7eb', borderRadius: '8px', padding: '8px 12px', fontSize: '14px', cursor: 'pointer'
  });
  b.onmouseenter = () => (b.style.background = 'rgba(59,130,246,.15)');
  b.onmouseleave = () => (b.style.background = 'rgba(11,14,19,0.35)');
  return b;
}

const btnHome  = mkBtn('AGV HOME');
const btnStat  = mkBtn('SYSTEM STATUS');
const btnPatrol = mkBtn('Full Patrol Once'); // NEW: Patrol button

uiBox.appendChild(btnHome);
uiBox.appendChild(btnStat);
uiBox.appendChild(btnPatrol); // NEW: Add patrol button to UI

const statPanel = ensureDom('agv-status-panel', 'pre', {
  position: 'fixed', top: '90px', right: '10px', zIndex: 1000, color: '#e5e7eb',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
  fontSize: '12px', lineHeight: '1.35', background: 'rgba(0,0,0,0.3)', padding: '10px 12px',
  borderRadius: '8px', border: '1px solid rgba(59,130,246,.25)', maxWidth: '36vw', maxHeight: '44vh',
  overflow: 'auto', margin: 0, whiteSpace: 'pre-wrap'
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

// =========================================================
// START: Example of adding a new object near the 1st column
// =========================================================

// 1. Define the object's geometry and material
// const sphereGeometry = new THREE.SphereGeometry(0.5, 32, 16); // radius 0.5, good detail
// const sphereMaterial = new THREE.MeshStandardMaterial({
//   color: 0xff0000, // A bright red color
//   metalness: 0.8,
//   roughness: 0.2
// });

// // 2. Create the Mesh
// const myNewSphere = new THREE.Mesh(sphereGeometry, sphereMaterial);

// // 3. Calculate its position
// // We use xPosFromIndex(1) to get the first column's X position.
// // Then we subtract some value (e.g., 1.5) to place it to the left.
// // We use yPosFromIndex(1) to align it with the first row.
// const newObjectX = xPosFromIndex(1) - 1.5; // -2.5 - 1.5 = -4.0
// const newObjectY = yPosFromIndex(1);       // Align with the first row
// const newObjectZ = 0;                      // Same depth as the cubes

// myNewSphere.position.set(newObjectX, newObjectY, newObjectZ);

// // 4. Add the object to the scene
// scene.add(myNewSphere);

// ----
/* ==================
   Machine Object
   ================== */
// This block creates a wireframe for a machine with a clickable control panel.

// 1. Define machine parameters
const MACHINE_WIDTH = 1.5 * CUBE_SIZE;
const MACHINE_HEIGHT = 6 * CUBE_SIZE;
const MACHINE_DEPTH = 2 * CUBE_SIZE;
const PANEL_THICKNESS = 0.2 * CUBE_SIZE;

// 2. Create a Group to hold all parts of the machine
const machine = new THREE.Group();

// 3. Create the main body of the machine (visual wireframe)
const machineBodyGeom = new THREE.BoxGeometry(MACHINE_WIDTH, MACHINE_HEIGHT, MACHINE_DEPTH);
const machineBodyEdges = new THREE.EdgesGeometry(machineBodyGeom);
const machineBodyLines = new THREE.LineSegments(machineBodyEdges, edgeMat); // Use the same blue edge material
machine.add(machineBodyLines);

// 4. Create the control panel
// This will have two parts: a visible wireframe and an invisible mesh for clicking.

// 4a. The visible wireframe for the panel
// const panelGeom = new THREE.BoxGeometry(MACHINE_WIDTH, PANEL_THICKNESS, MACHINE_DEPTH);
const panelGeom = new THREE.BoxGeometry(MACHINE_WIDTH * 0.8, PANEL_THICKNESS, MACHINE_DEPTH * 0.8);

const panelEdges = new THREE.EdgesGeometry(panelGeom);
const panelLines = new THREE.LineSegments(panelEdges, edgeMat);

// 4b. The invisible mesh for raycasting (clicking)
const panelPickMesh = new THREE.Mesh(panelGeom, invisibleMat);
panelPickMesh.userData = { type: 'machine_panel', name: 'Main Control Panel' };

// Position both panel parts on top of the machine body and rotate them
panelLines.position.x = 0;
panelLines.position.y = MACHINE_HEIGHT / 2 + .5;
panelLines.position.z = 0.5;
panelLines.rotation.x = Math.PI / 4; // Rotate 45 degrees up towards the camera
panelPickMesh.position.copy(panelLines.position);
panelPickMesh.rotation.copy(panelLines.rotation);

// Add panel parts to the machine group
machine.add(panelLines);
machine.add(panelPickMesh); // Add the invisible part to the group as well

// IMPORTANT: Add the invisible panel to the list of objects the mouse can interact with
pickMeshes.push(panelPickMesh);

// 5. Position the entire machine group in the scene
// We'll place it to the left of the first column of cubes.
const firstColumnX = xPosFromIndex(1); // X-center of the first column
const machineX = firstColumnX - (MACHINE_WIDTH / 2) - (CUBE_SIZE * 0.5) - 1; // Position next to it with a half-cube gap
const machineY = 0 +  (MACHINE_HEIGHT / 2); // Place its bottom on the grid floor
const machineZ = -0.5; // Align center-depth with the cubes

machine.position.set(machineX, machineY, machineZ);

// 6. Add the completed machine to the scene
scene.add(machine);


// =========================================================
// END: Example code
// =========================================================


const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

/* ==================================================
   MODIFIED: Status, Polling, and Automatic Capture
   ================================================== */
let isSystemBusy = false;
let wasSystemBusy = false; // Track the previous state

let currentClickX = null; // NEW: Store X for capture filename
let currentClickY = null; // NEW: Store Y for capture filename
let patrolResolveFunction = null; // NEW: For the patrol sequence to await step completion

// Function to trigger image capture
async function triggerCapture() {
    console.log("System is ready. Triggering capture...");
    try {
        const resp = await fetch('/capture', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: currentClickX, y: currentClickY }) // NEW: Pass x,y to backend
        });
        const data = await resp.json();
        if (resp.ok && data.ok) {
            console.log(`Capture successful: ${data.filename}`);
            statusEl.innerHTML += `<br/><span class="ok" style="color:#86efac">拍照成功: ${data.filename}</span>`;
        } else {
            console.error('Capture failed:', data.error);
            statusEl.innerHTML += `<br/><span class="err" style="color:#fca5a5">拍照失敗: ${data.error}</span>`;
        }
    } catch (err) {
        console.error('Error during capture request:', err);
        statusEl.innerHTML += `<br/><span class="err" style="color:#fca5a5">拍照請求失敗: ${err.message}</span>`;
    } finally {
        // NEW: Clear stored coordinates after capture
        currentClickX = null;
        currentClickY = null;
    }
}

function updateSystemBusyStatus(busy, message = "") {
  isSystemBusy = busy;
  if (isSystemBusy) {
    statusEl.innerHTML = `<span class="err" style="color:#fca5a5">系統忙碌中：${message || '上一個工作尚未完成'}，請稍候。</span>`;
    hideHover();
  } else {
    statusEl.innerHTML = `<span class="ok" style="color:#86efac">系統就緒：${message || '可點擊方塊送出新命令。'}</span>`;
  }
}

async function pollSystemStatus() {
  try {
    const resp = await fetch("/agv/status-summary");
    const data = await resp.json();
    if (resp.ok && data.system_busy !== undefined) {
      updateSystemBusyStatus(data.system_busy, data.details?.message || "執行中...");
      
      // *** Check for state transition: was busy, now not busy ***
      if (wasSystemBusy && !data.system_busy) {
        triggerCapture(); // It was busy, now it's not -> capture!
      }
      
      if (!data.system_busy) {
        stopPollingSystemStatus();
      }
    } else {
      console.error("Failed to fetch system status summary:", data);
      updateSystemBusyStatus(true, data.error || "Server 無法取得系統狀態。");
    }
  } catch (error) {
    console.error("Error polling system status:", error);
    updateSystemBusyStatus(true, `通訊錯誤：${error.message}`);
  } finally {
      // *** Update the previous state for the next poll ***
      wasSystemBusy = isSystemBusy;
  }
}

function startPollingSystemStatus() {
  if (systemPollingIntervalId === null) {
    wasSystemBusy = true; // Assume we are busy when starting to poll
    pollSystemStatus(); // Poll immediately
    systemPollingIntervalId = setInterval(pollSystemStatus, SYSTEM_POLL_RATE_MS);
  }
}

function stopPollingSystemStatus() {
  if (systemPollingIntervalId !== null) {
    clearInterval(systemPollingIntervalId);
    systemPollingIntervalId = null;
    if (patrolResolveFunction) { // NEW: Resolve the patrol promise if it exists
      patrolResolveFunction();
      patrolResolveFunction = null;
    }
  }
}

/* ===============
   Interactions
   =============== */
function renderRightTopStatus(objOrString, isError = false) {
  statPanel.style.display = 'block';
  if (typeof objOrString === 'string') {
    statPanel.textContent = objOrString;
  } else { statPanel.textContent = JSON.stringify(objOrString, null, 2); }
  statPanel.style.borderColor = isError ? 'rgba(252,165,165,.6)' : 'rgba(59,130,246,.25)';
}
function clearRightTopStatus() {
  statPanel.textContent = '';
  statPanel.style.display = 'none';
}

function sendClick(x, y) {
  if (isSystemBusy) { console.log("System busy; blocked new command."); return; }
  statusEl.textContent = `送出：x=${x}, y=${y} ...`;
  updateSystemBusyStatus(true, "正在分派任務...");

  // NEW: Store x,y for potential capture
  currentClickX = x;
  currentClickY = y;

  fetch("/click", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ x, y })
  })
  .then(async (resp) => {
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
        const errorMsg = data.error || '未知的錯誤';
        statusEl.innerHTML = `<span class="err" style="color:#fca5a5">指令失敗: ${errorMsg}</span>`;
        updateSystemBusyStatus(false, "指令被伺服器拒絕，請重試。");
        stopPollingSystemStatus();
        return;
    }
    statusEl.innerHTML = `狀態：<span class="ok" style="color:#86efac">OK</span> (任務已分派 x=${data.x}, y=${data.y})`;
    startPollingSystemStatus();
  })
  .catch(err => {
    statusEl.innerHTML = `狀態：<span class="err" style="color:#fca5a5">請求失敗: ${err.message || err}</span>`;
    updateSystemBusyStatus(false, `請求失敗，請檢查網路連線。`);
    stopPollingSystemStatus();
  });
}

// NEW: Helper function for patrol to send click and await completion
async function dispatchClickAndWait(x, y) {
    return new Promise((resolve) => {
        patrolResolveFunction = resolve; // Store the resolve function
        sendClick(x, y); // This initiates the command and starts polling
    });
}

// NEW: Full Patrol Once function with efficient "snake" pattern
async function startPatrol(repeatTimes = 1) {
    if (isSystemBusy) {
        alert("系統忙碌中。請等待當前操作完成後再開始巡邏。");
        return;
    }

    btnPatrol.disabled = true; // Disable patrol button during patrol

    for (let round = 1; round <= repeatTimes; round++) {
        statusEl.innerHTML = `<span style="color:#fcd34d">開始自動巡邏 (第 ${round}/${repeatTimes} 次)...</span>`;

        const AGV_X_POSITIONS = 5;  // AGV positions 1 to 6
        // const AGV_X_POSITIONS = 7;  // AGV positions 1 to 7
        const TRACK_Y_POSITIONS = 9; // Track positions 1 to 10

        for (let x = 1; x <= AGV_X_POSITIONS; x++) {
            // Create an array for the y-sequence based on whether x is odd or even
            const ySequence = [];
            const isOddColumn = x % 2 !== 0;

            if (isOddColumn) {
                for (let y = 6; y <= TRACK_Y_POSITIONS; y++) ySequence.push(y);
                // for (let y = 6; y <= 7; y++) ySequence.push(y);
                // change to fixed position for y=8
                // ySequence.push(7);
            } else {
                for (let y = TRACK_Y_POSITIONS; y >= 6; y--) ySequence.push(y);
                // for (let y = 7; y >= 6; y--) ySequence.push(y);
                // change to fixed position for y=8
                // ySequence.push(7);
            }

            // Iterate through the determined sequence for the current column
            for (const y of ySequence) {
                statusEl.innerHTML = `<span style="color:#fcd34d">巡邏中 (第 ${round}/${repeatTimes} 次): AGV位置 ${x}, 軌道位置 ${y} ...</span>`;
                
                // --- TRIGGER THE INDICATOR ANIMATION HERE ---
                animateIndicatorToPosition(x, y);                

                await dispatchClickAndWait(x, y); // Wait for this step (move + capture) to complete
                
                // Delay for robustness between patrol steps
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        }

        statusEl.innerHTML += `<br/><span class="ok">第 ${round} 次巡邏完成！</span>`;
    }

    statusEl.innerHTML += `<br/><span class="ok">全部 ${repeatTimes} 次巡邏完成！</span>`;
    btnPatrol.disabled = false; // Re-enable patrol button
}

/* =====================
   Animation Function
   ===================== */
/**
 * Smoothly animates the AGV indicator tube to a new grid position.
 * @param {number} gridX The target column index (1-based).
 * @param {number} gridY The target row index (1-based).
 */
function animateIndicatorToPosition(gridX, gridY) {
  if (!agvIndicatorTube) return; // Safety check for the new tube object

  // Calculate the target 3D coordinates from the grid indices
  const targetX = xPosFromIndex(gridX);

  // The bottom of a cube at gridY is at `yPosFromIndex(gridY) - (CUBE_SIZE / 2)`
  // The indicator's center should be half its height above that cube's bottom.
  const targetY =  -0.5 + (INDICATOR_HEIGHT / 2);

  const targetPosition = {
    x: targetX,
    y: targetY,
    z: CUBE_SIZE*2 // Keep the Z position constant, in front of the grid
  };

  // Use TWEEN.js to create the animation
  new TWEEN.Tween(agvIndicatorTube.position)
    .to(targetPosition, 5000) // SLOWER ANIMATION: 1500ms (was 750ms)
    .easing(TWEEN.Easing.Quadratic.InOut)
    .start();
}
/**
 * Smoothly animates the AGV indicator tube to the HOME position.
 * The HOME position is 2 cubes to the left of the first column.
 */
function animateIndicatorToHomePosition() {
  if (!agvIndicatorTube) return; // Safety check

  // Calculate the HOME position coordinates
  const homeX = xPosFromIndex(1) - (CUBE_SIZE / 2) - (2 * CUBE_SIZE);
  const homeY = -0.5 + (INDICATOR_HEIGHT / 2); // Ground level
  const homeZ = CUBE_SIZE * 2; // Same Z as other positions

  const targetPosition = { x: homeX, y: homeY, z: homeZ };

  // Use TWEEN.js to create the animation
  new TWEEN.Tween(agvIndicatorTube.position)
    .to(targetPosition, 1500) // Use the same slow speed
    .easing(TWEEN.Easing.Quadratic.InOut)
    .start();
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

  // NEW: Check if the clicked object is our machine panel (from previous changes)
  if (mesh.userData.type === 'machine_panel') {
    alert(`You clicked the ${mesh.userData.name}!`);
    console.log("Machine control panel clicked.", mesh.userData);
    return;
  }

  // This is the logic for handling cube clicks
  const { x, y } = mesh.userData;
  const worldNormal = hit.face.normal.clone().transformDirection(mesh.matrixWorld);

  if (worldNormal.dot(FRONT_NORMAL) > ANG_TOL) {
    // --- ADD ANIMATION CALL FOR FRONT FACE ---
    animateIndicatorToPosition(x, y);
    sendClick(x, y);
    return;
  }
  if (x === COLS && worldNormal.dot(RIGHT_NORMAL) > ANG_TOL) {
    // --- ADD ANIMATION CALL FOR RIGHT FACE (x=7) ---
    animateIndicatorToPosition(7, y);
    sendClick(7, y);
    return;
  }
}
renderer.domElement.addEventListener('mousemove', onMouseMove);
renderer.domElement.addEventListener('click', onClick);

/* ====================
   DOM button handlers
   ==================== */
btnHome.addEventListener('click', () => {
  if (isSystemBusy) return;

  // --- ADD THIS LINE TO ANIMATE THE INDICATOR ---
  animateIndicatorToHomePosition();

  clearRightTopStatus();
  statusEl.textContent = `送出：AGV HOME (1001) ...`;
  updateSystemBusyStatus(true, "正在發送 AGV HOME 指令...");

  // NEW: Clear x,y for HOME command as it's not a specific position
  currentClickX = 'home';
  currentClickY = 'home';

  fetch('/agv/home', { method: 'POST', headers: {'Content-Type':'application/json'} })
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

// btnPatrol.addEventListener('click', startPatrol); // NEW: Attach patrol function to button
btnPatrol.addEventListener('click', () => startPatrol(20));


/**
 * On page load, fetches the AGV status to set the indicator's initial position.
 * This version correctly uses the `location` field from the AGV status.
 */
async function initializeIndicatorPosition() {
  console.log("Initializing AGV indicator from current location...");
  try {
    const resp = await fetch("/agv/status-summary");
    if (!resp.ok) {
      console.error("Failed to fetch initial AGV status. Defaulting indicator to HOME.");
      // Fallback to home if the request fails
      const homeX = xPosFromIndex(1) - (CUBE_SIZE / 2) - (2 * CUBE_SIZE);
      agvIndicatorTube.position.set(homeX, -0.5 + (INDICATOR_HEIGHT / 2), CUBE_SIZE * 2);
      return;
    }
    const data = await resp.json();

    // --- THIS IS THE KEY CORRECTION ---
    // Prioritize the reliable `location` field. Fall back to `targetTag` only if needed.
    const locationTag = data?.agv_raw_status?.location || data?.agv_raw_status?.task?.targetTag;
    console.log(`Detected AGV location tag from server: ${locationTag}`);

    const agvX = TARGET_TAG_TO_X[locationTag];
    console.log(`Mapped location to frontend x-coordinate: ${agvX}`);

    if (agvX === 'home') {
      // INSTANTLY set position to HOME
      const homeX = xPosFromIndex(1) - (CUBE_SIZE / 2) - (2 * CUBE_SIZE);
      agvIndicatorTube.position.set(homeX, -0.5 + (INDICATOR_HEIGHT / 2), CUBE_SIZE * 2);
      console.log("Indicator set to HOME position.");

    } else if (typeof agvX === 'number' && agvX >= 1 && agvX <= 7) {
      // INSTANTLY set position to the correct column
      const targetX = xPosFromIndex(agvX);
      agvIndicatorTube.position.set(targetX, -0.5 + (INDICATOR_HEIGHT / 2), CUBE_SIZE * 2);
      console.log(`Indicator instantly set to position x=${agvX}.`);

    } else {
      // Default to HOME if the location is unknown, null, or not in our map
      console.log(`AGV location "${locationTag}" is unknown or unmapped, defaulting indicator to HOME.`);
      const homeX = xPosFromIndex(1) - (CUBE_SIZE / 2) - (2 * CUBE_SIZE);
      agvIndicatorTube.position.set(homeX, -0.5 + (INDICATOR_HEIGHT / 2), CUBE_SIZE * 2);
    }
  } catch (error) {
    console.error("Error during indicator initialization:", error);
    // Also default to HOME if any other error occurs
    const homeX = xPosFromIndex(1) - (CUBE_SIZE / 2) - (2 * CUBE_SIZE);
    agvIndicatorTube.position.set(homeX, -0.5 + (INDICATOR_HEIGHT / 2), CUBE_SIZE * 2);
  }
}
/* =============
   Render loop
   ============= */
function loop(){
  TWEEN.update(); // <-- ADD THIS LINE to update animations every frame

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

// Set indicator to correct position on page load, then start regular polling
initializeIndicatorPosition();
pollSystemStatus();
