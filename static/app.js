// Use ES modules from CDN (pin to r160 to match API)
// Pin to r160; use full URLs so the browser can load them without import maps
import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js?module';
import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js?module';

// ===== 參數 =====
const CUBE_SIZE = 1;                 // 1 單位 = 1 cm
const COLS = 6;                      // x: 1..6
const ROWS = 10;                     // y: 1..10
const FRONT_NORMAL = new THREE.Vector3(0, 0, 1);  // 正面 +Z
const RIGHT_NORMAL = new THREE.Vector3(1, 0, 0);  // 右側 +X
const ANG_TOL = 0.9;                 // cosθ 門檻
const BLUE = 0x3b82f6;

// ===== 你想要的初始視角（可先用預設，調整好後再覆寫成你要的數值）=====
const INITIAL_VIEW = {
  // 相機位置
  position: { x: 14.498380, y: 7.191459, z: 17.062261 },
  // 觀看目標（平移的基準點）
  target:   { x: 0.329985, y: 3.830037, z: -0.117474 },
  // 透視相機視角（度）。若用正交相機，改用 zoom 參數。
  fov: 55.000,
  // orthographic 相機才會用到；perspective 一般保持 1
  zoom: 1
};

// ===== AGV Status Polling Config =====
const AGV_STATUS_POLL_INTERVAL_MS = 2000; // Poll every 2 seconds

// ===== 場景 =====
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0e13);

const camera = new THREE.PerspectiveCamera(
  INITIAL_VIEW.fov,
  window.innerWidth / window.innerHeight,
  0.1,
  1000
);

// 套用初始視角
camera.position.set(INITIAL_VIEW.position.x, INITIAL_VIEW.position.y, INITIAL_VIEW.position.z);
camera.zoom = INITIAL_VIEW.zoom;
camera.updateProjectionMatrix();

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

// 控制器：左旋轉、右平移、滾輪縮放
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.mouseButtons = {
  LEFT: THREE.MOUSE.ROTATE,
  MIDDLE: THREE.MOUSE.DOLLY,
  RIGHT: THREE.MOUSE.PAN
};
controls.minDistance = 3;
controls.maxDistance = 30;

// 設定 OrbitControls 的 target（平移是改這個點）
controls.target.set(INITIAL_VIEW.target.x, INITIAL_VIEW.target.y, INITIAL_VIEW.target.z);
controls.update();

// 簡單燈光（僅為高亮面參考）
scene.add(new THREE.HemisphereLight(0x8899aa, 0x111111, 0.7));
const dir = new THREE.DirectionalLight(0xffffff, 0.4);
dir.position.set(3,5,6);
scene.add(dir);

// 地面格線（可選）
const grid = new THREE.GridHelper(40, 40, 0x334155, 0x1f2937);
grid.position.y = -0.51;
scene.add(grid);

// ===== 幾何與材質 =====
const boxGeom = new THREE.BoxGeometry(CUBE_SIZE, CUBE_SIZE, CUBE_SIZE);
const invisibleMat = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.0 });
const edgeMat = new THREE.LineBasicMaterial({ color: BLUE });

// 高亮用的紅色平面（hover 時顯示）
const hoverPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(CUBE_SIZE, CUBE_SIZE),
  new THREE.MeshBasicMaterial({ color: 0xff2d2e, transparent: true, opacity: 0.6, side: THREE.DoubleSide })
);
hoverPlane.visible = false;
scene.add(hoverPlane);

// ===== 建立 6×10 立方體：不可見 Mesh（供 raycast）+ 藍色線框 =====
function xPosFromIndex(ix) { return (ix - (COLS + 1) / 2) * CUBE_SIZE; } // 置中
function yPosFromIndex(iy) { return (iy - 0.5) * CUBE_SIZE; }            // 底部從 0.5 開始
const zPos = 0;

const pickMeshes = []; // 用來做 raycast 的不可見 Mesh
for (let ix = 1; ix <= COLS; ix++) {
  for (let iy = 1; iy <= ROWS; iy++) {
    const mesh = new THREE.Mesh(boxGeom, invisibleMat);
    mesh.position.set(xPosFromIndex(ix), yPosFromIndex(iy), zPos);
    mesh.userData = { x: ix, y: iy };
    scene.add(mesh);
    pickMeshes.push(mesh);

    // 線框
    const edges = new THREE.EdgesGeometry(boxGeom, 1);
    const line = new THREE.LineSegments(edges, edgeMat);
    line.position.copy(mesh.position);
    scene.add(line);
  }
}

// ===== Raycaster & 互動 =====
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const statusEl = document.getElementById('status');
let isAgvBusy = false; // New state variable for AGV busy status

// Function to update the AGV busy status and UI
function updateAgvBusyStatus(busy, message = "") {
  isAgvBusy = busy;
  if (isAgvBusy) {
    statusEl.innerHTML = `<span class="err">AGV is busy: ${message || 'The previous job has not completed.'} Please wait.</span>`;
  } else {
    // Only clear the error message if it was an AGV busy message
    if (statusEl.innerHTML.includes("AGV is busy")) {
      statusEl.textContent = `AGV is ready. Click a cube to send commands.`;
    }
  }
}

// Polling function for AGV status
async function pollAgvStatus() {
  try {
    const resp = await fetch("/agv/status-summary"); // New Flask endpoint
    const data = await resp.json();

    if (resp.ok && data.agv_busy !== undefined) {
      updateAgvBusyStatus(data.agv_busy, data.agv_status_message);
    } else {
      console.error("Failed to fetch AGV status summary:", data);
      updateAgvBusyStatus(true, data.error || "Could not get AGV status from server.");
    }
  } catch (error) {
    console.error("Error polling AGV status:", error);
    updateAgvBusyStatus(true, `Server communication error: ${error.message}`);
  } finally {
    setTimeout(pollAgvStatus, AGV_STATUS_POLL_INTERVAL_MS);
  }
}

// 送出點擊
function sendClick(x, y) {
  if (isAgvBusy) {
    console.log("AGV is busy, preventing new command.");
    // UI message already set by updateAgvBusyStatus
    return;
  }

  statusEl.textContent = `送出：x=${x}, y=${y} ...`;
  // Temporarily set AGV as busy to prevent rapid clicks while waiting for Flask response
  updateAgvBusyStatus(true, "Sending command..."); 

  fetch("/click", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y })
  })
  .then(async (resp) => {
    let d;
    try {
      d = await resp.json();
    } catch (e) {
      throw new Error(`Invalid JSON response: ${e}`);
    }

    if (d.agv_result && d.agv_result.busy) { // Flask reported AGV was busy AFTER fetch
      updateAgvBusyStatus(true, d.agv_result.error || `AGV is busy, status: ${d.agv_result.status}`);
      return;
    }
    
    // Check overall `ok` status
    if (!d.ok) {
      statusEl.innerHTML = `<span class="err">Error: ${d.error || (d.agv_result ? d.agv_result.error : 'unknown')}</span>`;
      updateAgvBusyStatus(true, d.error || (d.agv_result ? d.agv_result.error : 'unknown')); // Keep busy on general error
      return;
    }

    // If both AGV and track are good
    statusEl.innerHTML = `狀態：<span class="ok">OK</span> （伺服器回覆 x=${d.x}, y=${d.y}）`;
    // AGV might be busy now due to the new task, or it might report not busy yet.
    // The polling mechanism will eventually catch the real status.
    updateAgvBusyStatus(true, "AGV task initiated, waiting for completion..."); 
  })
  .catch(err => {
    statusEl.innerHTML =
      `狀態：<span class="err">Request failed: ${err.message || err}</span>`;
    updateAgvBusyStatus(true, `Request failed: ${err.message || err}`); // Keep busy on network errors
  });
}

// 由滑鼠座標作 raycast
function setPointerFromEvent(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  const px = (event.clientX - rect.left) / rect.width;
  const py = (event.clientY - rect.top) / rect.height;
  pointer.set(px * 2 - 1, - (py * 2 - 1));
}

// hover 高亮（面）定位
const EPS = 0.002; // 避免 Z-fighting 的微小偏移
function showHoverFace(mesh, faceType /* 'front'|'right' */) {
  if (isAgvBusy) return; // Don't show hover if busy

  hoverPlane.visible = true;
  hoverPlane.position.copy(mesh.position);
  hoverPlane.rotation.set(0,0,0); // reset

  if (faceType === 'front') {
    // 正面在 z = +0.5
    hoverPlane.position.z += CUBE_SIZE/2 + EPS;
  } else if (faceType === 'right') {
    // 右側面在 x = +0.5，且面朝 +X，需旋轉面使其朝 +X
    hoverPlane.position.x += CUBE_SIZE/2 + EPS;
    hoverPlane.rotation.y = -Math.PI/2;
  }
}

// 清除 hover
function hideHover() {
  hoverPlane.visible = false;
}

// 滑鼠移動：更新 hover 面
function onMouseMove(event) {
  if (isAgvBusy) { // If busy, just hide hover and return
    hideHover();
    return;
  }
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(pickMeshes, false);
  if (!hits.length) { hideHover(); return; }

  const hit = hits[0];
  const mesh = hit.object;
  const { x } = mesh.userData;

  // face normal 轉到 world 判斷哪一面
  const worldNormal = hit.face.normal.clone().transformDirection(mesh.matrixWorld);

  if (worldNormal.dot(FRONT_NORMAL) > ANG_TOL) {
    showHoverFace(mesh, 'front');
    return;
  }
  if (x === COLS && worldNormal.dot(RIGHT_NORMAL) > ANG_TOL) {
    showHoverFace(mesh, 'right');
    return;
  }
  hideHover();
}

// 滑鼠點擊：依面型送出
function onClick(event) {
  if (isAgvBusy) {
    console.log("AGV is busy, preventing click action.");
    return;
  }
  setPointerFromEvent(event);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(pickMeshes, false);
  if (!hits.length) return;

  const hit = hits[0];
  const mesh = hit.object;
  const { x, y } = mesh.userData;
  const worldNormal = hit.face.normal.clone().transformDirection(mesh.matrixWorld);

  if (worldNormal.dot(FRONT_NORMAL) > ANG_TOL) {
    sendClick(x, y);
    return;
  }
  if (x === COLS && worldNormal.dot(RIGHT_NORMAL) > ANG_TOL) {
    sendClick(7, y);
    return;
  }
}

renderer.domElement.addEventListener('mousemove', onMouseMove);
renderer.domElement.addEventListener('click', onClick);

// ===== 動畫 =====
function animate() {
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
animate();

// Resize
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// Start AGV status polling when the page loads
window.onload = () => {
  pollAgvStatus();
};


/* 下面的程式碼可協助你調整好視角後，直接把目前視角輸出成程式碼片段，貼回上方 INITIAL_VIEW 使用。
   如果不需要就註解掉即可。 */

// 將相機位置與控制器 target 轉成「距離/方位角/俯仰角」等可讀數值
// function logView(label = 'view') {
//   const t = controls.target.clone();
//   const p = camera.position.clone();
//   const offset = p.clone().sub(t);                 // 相機相對 target 的向量
//   const sph = new THREE.Spherical().setFromVector3(offset);

//   // 角度：azimuth=水平（繞 Y 軸，右為正），polar=俯仰（0=北極直視、180=南極）
//   const azimuthDeg = THREE.MathUtils.radToDeg(sph.theta);
//   const polarDeg   = THREE.MathUtils.radToDeg(sph.phi);

//   // 透視相機縮放是靠改變距離（sph.radius）；正交相機則看 camera.zoom
//   const zoomOrDist = camera.isPerspectiveCamera
//     ? `distance=${sph.radius.toFixed(3)}`
//     : `zoom=${camera.zoom.toFixed(3)}`;
//   // 輸出成「可直接貼回程式」的片段
//   const codeSnippet = `
// const INITIAL_VIEW = {
//   position: { x: ${p.x.toFixed(6)}, y: ${p.y.toFixed(6)}, z: ${p.z.toFixed(6)} },
//   target:   { x: ${t.x.toFixed(6)}, y: ${t.y.toFixed(6)}, z: ${t.z.toFixed(6)} },
//   fov: ${camera.isPerspectiveCamera ? camera.fov.toFixed(3) : '/* n/a (ortho) */'},
//   zoom: ${camera.zoom.toFixed(3)}
// };`.trim();
//   console.log(
//     `[${label}]`,
//     `pos=(${p.x.toFixed(3)}, ${p.y.toFixed(3)}, ${p.z.toFixed(3)})`,
//     `target=(${t.x.toFixed(3)}, ${t.y.toFixed(3)}, ${t.z.toFixed(3)})`,
//     `azimuth=${azimuthDeg.toFixed(1)}°`,
//     `polar=${polarDeg.toFixed(1)}°`,
//     camera.isPerspectiveCamera ? `fov=${camera.fov.toFixed(2)}` : '',
//     zoomOrDist
//   );
//   console.log('Paste this into INITIAL_VIEW to fix your start view:\n' + codeSnippet);
// }
// // 每次使用者「旋轉/平移/縮放」都印出一次
// controls.addEventListener('change', () => logView('change'));
// // 也可以提供一個全域函式，讓你在 Console 想印就印：window.dumpView()
// window.dumpView = () => logView('manual');
// // 啟動時印一次當前初始視角
// logView('initial');