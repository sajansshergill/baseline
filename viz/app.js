import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

const WEEKS = 52;
const START = new Date(Date.UTC(2025, 8, 1)); // 2025-09-01

const STRUCTURES = [
  { id: "civil-a", name: "Building A civil", x: -18, z: -8, w: 14, d: 22, floors: 4, start: 0, end: 16, risk: 0.15 },
  { id: "shell-a", name: "Building A shell", x: -18, z: -8, w: 13.2, d: 21, floors: 6, start: 10, end: 28, risk: 0.22 },
  { id: "fitout-a", name: "Building A fit-out", x: -18, z: -8, w: 12.4, d: 20, floors: 6, start: 22, end: 44, risk: 0.35 },
  { id: "civil-b", name: "Building B civil", x: 8, z: -6, w: 12, d: 18, floors: 3, start: 6, end: 22, risk: 0.18 },
  { id: "shell-b", name: "Building B shell", x: 8, z: -6, w: 11.2, d: 17, floors: 5, start: 16, end: 34, risk: 0.28 },
  { id: "fitout-b", name: "Building B fit-out", x: 8, z: -6, w: 10.4, d: 16, floors: 5, start: 28, end: 50, risk: 0.4 },
  { id: "switchgear", name: "Switchgear yard", x: -6, z: 16, w: 10, d: 8, floors: 1, start: 18, end: 36, risk: 0.55 },
  { id: "gens", name: "Generator farm", x: 10, z: 16, w: 12, d: 7, floors: 1, start: 20, end: 40, risk: 0.6 },
  { id: "cooling", name: "Cooling plant", x: -26, z: 10, w: 8, d: 10, floors: 2, start: 24, end: 42, risk: 0.45 },
  { id: "substation", name: "Utility substation", x: 22, z: 4, w: 7, d: 9, floors: 2, start: 12, end: 30, risk: 0.32 },
];

const CRITICAL = ["civil-a", "shell-a", "fitout-a", "switchgear", "gens"];

function weekDate(week) {
  const d = new Date(START);
  d.setUTCDate(d.getUTCDate() + week * 7);
  return d.toISOString().slice(0, 10);
}

function healthFor(week) {
  const base = 92 - week * 0.22;
  const dips = week > 30 ? 6 : week > 18 ? 3 : 0;
  return Math.max(68, Math.min(98, base - dips + Math.sin(week / 4) * 2));
}

function slipFor(week) {
  return Math.min(0.92, 0.08 + week * 0.012 + (week > 28 ? 0.12 : 0));
}

function focusFor(week) {
  const active = STRUCTURES.filter((s) => week >= s.start && week <= s.end)
    .sort((a, b) => b.risk - a.risk);
  return active[0]?.name ?? (week < 4 ? "Site mobilization" : "Commissioning & punch");
}

const stage = document.getElementById("stage");
const weekLabel = document.getElementById("weekLabel");
const dateLabel = document.getElementById("dateLabel");
const healthLabel = document.getElementById("healthLabel");
const slipLabel = document.getElementById("slipLabel");
const focusLabel = document.getElementById("focusLabel");
const playBtn = document.getElementById("playBtn");
const scrubber = document.getElementById("scrubber");
const orbitBtn = document.getElementById("orbitBtn");

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.25;
renderer.setClearColor(0x0b1018, 1);
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1018);
scene.fog = new THREE.FogExp2(0x0b1018, 0.012);

const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 250);
camera.position.set(48, 34, 52);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.target.set(0, 4, 0);
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 18;
controls.maxDistance = 110;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.45;

scene.add(new THREE.AmbientLight(0x9aa8b8, 0.75));
const key = new THREE.DirectionalLight(0xd7e6ff, 1.45);
key.position.set(30, 50, 20);
scene.add(key);
const fill = new THREE.DirectionalLight(0x2fd4c2, 0.55);
fill.position.set(-25, 20, -10);
scene.add(fill);
const rim = new THREE.PointLight(0xe8a838, 1.8, 90);
rim.position.set(0, 18, 24);
scene.add(rim);
const hemi = new THREE.HemisphereLight(0x8ec8ff, 0x1a1510, 0.45);
scene.add(hemi);

const ground = new THREE.Mesh(
  new THREE.CircleGeometry(70, 64),
  new THREE.MeshStandardMaterial({ color: 0x12161d, roughness: 0.95, metalness: 0.05 }),
);
ground.rotation.x = -Math.PI / 2;
scene.add(ground);

const grid = new THREE.GridHelper(80, 40, 0x2a3340, 0x1a2029);
grid.position.y = 0.02;
scene.add(grid);

// Site pad outline
const pad = new THREE.Mesh(
  new THREE.PlaneGeometry(56, 42),
  new THREE.MeshStandardMaterial({ color: 0x171c24, roughness: 0.9, metalness: 0.1 }),
);
pad.rotation.x = -Math.PI / 2;
pad.position.set(-2, 0.03, 2);
scene.add(pad);

const buildingGroup = new THREE.Group();
scene.add(buildingGroup);

const materials = {
  idle: new THREE.MeshStandardMaterial({ color: 0x2a313c, roughness: 0.85, metalness: 0.2, transparent: true, opacity: 0.25 }),
  done: new THREE.MeshStandardMaterial({ color: 0x6f7f90, roughness: 0.7, metalness: 0.25 }),
  active: new THREE.MeshStandardMaterial({ color: 0x2fd4c2, roughness: 0.35, metalness: 0.55, emissive: 0x0a3d38, emissiveIntensity: 0.55 }),
  risk: new THREE.MeshStandardMaterial({ color: 0xe8a838, roughness: 0.4, metalness: 0.45, emissive: 0x5a3a08, emissiveIntensity: 0.7 }),
  critical: new THREE.MeshStandardMaterial({ color: 0xff5c4d, roughness: 0.35, metalness: 0.5, emissive: 0x4a120f, emissiveIntensity: 0.8 }),
};

const meshes = STRUCTURES.map((spec) => {
  const floorH = 2.2;
  const geo = new THREE.BoxGeometry(spec.w, floorH * spec.floors, spec.d);
  geo.translate(0, (floorH * spec.floors) / 2, 0);
  const mesh = new THREE.Mesh(geo, materials.idle.clone());
  mesh.position.set(spec.x, 0, spec.z);
  mesh.userData = { spec, baseH: floorH * spec.floors };
  buildingGroup.add(mesh);

  const edges = new THREE.EdgesGeometry(geo);
  const line = new THREE.LineSegments(
    edges,
    new THREE.LineBasicMaterial({ color: 0x9eb0c2, transparent: true, opacity: 0.35 }),
  );
  mesh.add(line);
  return mesh;
});

// Crane
const crane = new THREE.Group();
const mast = new THREE.Mesh(
  new THREE.BoxGeometry(0.7, 28, 0.7),
  new THREE.MeshStandardMaterial({ color: 0xe8a838, metalness: 0.7, roughness: 0.35 }),
);
mast.position.y = 14;
crane.add(mast);
const jib = new THREE.Mesh(
  new THREE.BoxGeometry(22, 0.45, 0.45),
  new THREE.MeshStandardMaterial({ color: 0xe8a838, metalness: 0.7, roughness: 0.35 }),
);
jib.position.set(8, 27.5, 0);
crane.add(jib);
const cab = new THREE.Mesh(
  new THREE.BoxGeometry(2.2, 1.4, 2.2),
  new THREE.MeshStandardMaterial({ color: 0x2fd4c2, emissive: 0x0a3d38, emissiveIntensity: 0.4 }),
);
cab.position.set(0, 26.5, 0);
crane.add(cab);
crane.position.set(-4, 0, 6);
scene.add(crane);

// Sparks / dust
const sparkCount = 400;
const sparkGeo = new THREE.BufferGeometry();
const sparkPos = new Float32Array(sparkCount * 3);
for (let i = 0; i < sparkCount; i++) {
  sparkPos[i * 3] = (Math.random() - 0.5) * 60;
  sparkPos[i * 3 + 1] = Math.random() * 24;
  sparkPos[i * 3 + 2] = (Math.random() - 0.5) * 50;
}
sparkGeo.setAttribute("position", new THREE.BufferAttribute(sparkPos, 3));
const sparks = new THREE.Points(
  sparkGeo,
  new THREE.PointsMaterial({ color: 0x2fd4c2, size: 0.12, transparent: true, opacity: 0.55 }),
);
scene.add(sparks);

const pathGroup = new THREE.Group();
scene.add(pathGroup);

function rebuildCriticalPath(week) {
  while (pathGroup.children.length) {
    const child = pathGroup.children.pop();
    child.geometry?.dispose();
    child.material?.dispose();
  }
  const live = CRITICAL.filter((id) => {
    const s = STRUCTURES.find((x) => x.id === id);
    return s && week >= s.start && week <= s.end + 4;
  });
  for (let i = 0; i < live.length - 1; i++) {
    const a = STRUCTURES.find((x) => x.id === live[i]);
    const b = STRUCTURES.find((x) => x.id === live[i + 1]);
    const points = [
      new THREE.Vector3(a.x, a.floors * 1.4, a.z),
      new THREE.Vector3((a.x + b.x) / 2, Math.max(a.floors, b.floors) * 1.8 + 4, (a.z + b.z) / 2),
      new THREE.Vector3(b.x, b.floors * 1.4, b.z),
    ];
    const curve = new THREE.CatmullRomCurve3(points);
    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 48, 0.12, 8, false),
      new THREE.MeshStandardMaterial({
        color: 0xff5c4d,
        emissive: 0xff5c4d,
        emissiveIntensity: 0.9,
        transparent: true,
        opacity: 0.85,
      }),
    );
    pathGroup.add(tube);
  }
}

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.55, 0.7, 0.85);
composer.addPass(bloom);

let week = 0;
let playing = true;
let freeCam = false;
let lastTick = 0;

function applyWeek(w) {
  week = Math.max(0, Math.min(WEEKS - 1, w));
  scrubber.value = String(week);
  weekLabel.textContent = `${String(week + 1).padStart(2, "0")} / ${WEEKS}`;
  dateLabel.textContent = weekDate(week);
  healthLabel.textContent = `${healthFor(week).toFixed(0)}`;
  slipLabel.textContent = `${(slipFor(week) * 100).toFixed(0)}%`;
  focusLabel.textContent = focusFor(week);

  for (const mesh of meshes) {
    const { spec } = mesh.userData;
    let progress = 0;
    if (week < spec.start) progress = 0;
    else if (week >= spec.end) progress = 1;
    else progress = Math.max(0.08, (week - spec.start + 1) / (spec.end - spec.start + 1));

    const upcoming = week < spec.start && week >= spec.start - 3;
    const eased = progress * progress * (3 - 2 * progress);
    mesh.visible = progress > 0 || upcoming;

    const mat = mesh.material;
    const onCritical = CRITICAL.includes(spec.id) && progress > 0 && progress < 1;
    const risky = progress > 0 && progress < 1 && spec.risk > 0.4 && week > spec.start + 4;
    if (upcoming && progress <= 0) {
      mat.color.copy(materials.idle.color);
      mat.emissive.setHex(0x000000);
      mat.emissiveIntensity = 0;
      mat.opacity = 0.18;
      mesh.scale.set(1, 0.04, 1);
    } else if (progress >= 1) {
      mat.color.copy(materials.done.color);
      mat.emissive.setHex(0x000000);
      mat.emissiveIntensity = 0;
      mat.opacity = 1;
      mesh.scale.set(1, 1, 1);
    } else if (onCritical) {
      mat.color.copy(materials.critical.color);
      mat.emissive.setHex(0x4a120f);
      mat.emissiveIntensity = 0.75 + Math.sin(performance.now() / 220) * 0.15;
      mat.opacity = 1;
      mesh.scale.set(1, Math.max(0.08, eased), 1);
    } else if (risky) {
      mat.color.copy(materials.risk.color);
      mat.emissive.setHex(0x5a3a08);
      mat.emissiveIntensity = 0.65;
      mat.opacity = 1;
      mesh.scale.set(1, Math.max(0.08, eased), 1);
    } else {
      mat.color.copy(materials.active.color);
      mat.emissive.setHex(0x0a3d38);
      mat.emissiveIntensity = 0.55;
      mat.opacity = 1;
      mesh.scale.set(1, Math.max(0.08, eased), 1);
    }
  }

  crane.visible = week >= 2 && week <= 48;
  crane.rotation.y = week * 0.35;
  crane.position.y = Math.sin(week / 3) * 0.15;
  rebuildCriticalPath(week);
}

playBtn.addEventListener("click", () => {
  playing = !playing;
  playBtn.textContent = playing ? "Pause" : "Play";
});

scrubber.addEventListener("input", () => {
  playing = false;
  playBtn.textContent = "Play";
  applyWeek(Number(scrubber.value));
});

orbitBtn.addEventListener("click", () => {
  freeCam = !freeCam;
  controls.autoRotate = !freeCam;
  orbitBtn.textContent = freeCam ? "Auto orbit" : "Free cam";
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  composer.setSize(window.innerWidth, window.innerHeight);
  bloom.setSize(window.innerWidth, window.innerHeight);
});

function animate(t) {
  requestAnimationFrame(animate);
  if (playing && t - lastTick > 420) {
    lastTick = t;
    applyWeek((week + 1) % WEEKS);
  }

  const positions = sparks.geometry.attributes.position;
  for (let i = 0; i < sparkCount; i++) {
    let y = positions.getY(i) + 0.02 + (i % 5) * 0.004;
    if (y > 26) y = 0.2;
    positions.setY(i, y);
  }
  positions.needsUpdate = true;
  sparks.rotation.y += 0.0008;

  pathGroup.children.forEach((tube, i) => {
    tube.material.emissiveIntensity = 0.7 + Math.sin(t / 280 + i) * 0.25;
  });

  controls.update();
  composer.render();
}

applyWeek(0);
animate(0);
