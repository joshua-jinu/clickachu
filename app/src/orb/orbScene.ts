/**
 * The orb (PLAN §9.1): ~2,500 points on a Fibonacci sphere plus a faint lat/long wireframe, both
 * displaced radially by 3D simplex noise in the vertex shader, additively blended, coloured
 * blue → violet → magenta, with sparse twinkling sparkles around it.
 *
 * All per-vertex work is on the GPU; the CPU only lerps a handful of uniforms per frame.
 * 30 fps when idle and settled, 60 fps otherwise, nothing while the document is hidden.
 */
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Group,
  LineSegments,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  WebGLRenderer,
} from 'three'
import type { OrbStateName } from '@shared/protocol'

const POINT_COUNT = 2500
const SPARKLE_COUNT = 56
/** Seconds for a state change to settle (~95 %). */
const TRANSITION_S = 0.3
const IDLE_FPS = 30
const ACTIVE_FPS = 60
const SHAKE_S = 0.5

const COLOURS = ['#4f7cff', '#8b5cf6', '#e040fb'].map((c) => new Color(c))
const CYAN = new Color('#3fe0ff')
const VIOLET_GLOW = new Color('#a78bfa')
const MAGENTA_GLOW = new Color('#e879f9')
const RED = new Color('#ff3b5c')

export type OrbVisualState = Exclude<OrbStateName, 'hidden'> | 'speaking' | 'typing'

interface Params {
  noiseSpeed: number
  /** Radial noise displacement, fraction of radius. */
  amp: number
  breathAmp: number
  breathHz: number
  pulseAmp: number
  pulseHz: number
  /** Rotation, rad/s. */
  spin: number
  brightness: number
  /** 0 = base gradient, 1 = fully the shift colour. */
  shift: number
}

const BASE: Params = {
  noiseSpeed: 0.25,
  amp: 0.07,
  breathAmp: 0.03,
  breathHz: 0.2,
  pulseAmp: 0,
  pulseHz: 1,
  spin: 0.18,
  brightness: 1,
  shift: 0,
}

// §9.1 state table + HUD conversational states.
const STATES: Record<OrbVisualState, { params: Partial<Params>; shiftColour: Color }> = {
  idle: { params: {}, shiftColour: CYAN },
  listening: {
    params: { noiseSpeed: 0.55, amp: 0.15, breathAmp: 0.01, shift: 0.65, spin: 0.42, brightness: 1.2 },
    shiftColour: CYAN,
  },
  typing: {
    params: { noiseSpeed: 0.28, amp: 0.045, breathAmp: 0.025, breathHz: 0.3, spin: 0.24, brightness: 1.18, shift: 0.28 },
    shiftColour: VIOLET_GLOW,
  },
  thinking: {
    params: { noiseSpeed: 0.85, amp: 0.11, spin: 1.45, brightness: 1.2 },
    shiftColour: CYAN,
  },
  speaking: {
    params: { noiseSpeed: 0.65, amp: 0.14, breathAmp: 0.015, pulseAmp: 0.04, pulseHz: 2.0, spin: 0.55, brightness: 1.35, shift: 0.42 },
    shiftColour: MAGENTA_GLOW,
  },
  acting: {
    params: { amp: 0.09, breathAmp: 0, pulseAmp: 0.07, pulseHz: 1.6, spin: 0.5, brightness: 1.4 },
    shiftColour: CYAN,
  },
  attention: { params: { breathAmp: 0, pulseAmp: 0.04, pulseHz: 0.8 }, shiftColour: CYAN },
  error: { params: { shift: 0.7, spin: 0.1 }, shiftColour: RED },
}

const SIMPLEX_GLSL = /* glsl */ `
// 3D simplex noise — Ian McEwan, Ashima Arts (MIT). https://github.com/ashima/webgl-noise
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
      i.z + vec4(0.0, i1.z, i2.z, 1.0))
    + i.y + vec4(0.0, i1.y, i2.y, 1.0))
    + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`

const SPHERE_VERTEX = /* glsl */ `
uniform float uNoiseTime;
uniform float uAmp;
uniform float uScale;
uniform float uPointSize;
attribute float aRand;
varying float vT;
varying float vFacing;
${SIMPLEX_GLSL}
void main() {
  float n = snoise(position * 1.7 + vec3(0.0, 0.0, uNoiseTime));
  vec4 mv = modelViewMatrix * vec4(position * (uScale + uAmp * n), 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = uPointSize * (0.7 + 0.6 * aRand) * (1.0 + 0.4 * n);
  // Gradient runs bottom → top, wobbled by the same noise so colour flows with the surface.
  vT = clamp(position.y * 0.5 + 0.5 + n * 0.18, 0.0, 1.0);
  // +1 facing the camera, -1 on the far side.
  vFacing = normalize(mat3(modelViewMatrix) * position).z;
}
`

const SPHERE_FRAGMENT = /* glsl */ `
uniform vec3 uC0;
uniform vec3 uC1;
uniform vec3 uC2;
uniform vec3 uShiftColour;
uniform float uShift;
uniform float uBrightness;
uniform float uOpacity;
varying float vT;
varying float vFacing;
void main() {
  #ifdef POINTS
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float a = 1.0 - d * 2.0;
    a *= a;
  #else
    float a = 1.0;
  #endif
  vec3 base = vT < 0.5 ? mix(uC0, uC1, vT * 2.0) : mix(uC1, uC2, vT * 2.0 - 1.0);
  vec3 col = min(mix(base, uShiftColour, uShift) * uBrightness, vec3(1.0));
  float alpha = a * uOpacity * mix(0.3, 1.0, vFacing * 0.5 + 0.5);
  // Premultiplied output; with premultipliedAlpha the additive blend is ONE, ONE.
  gl_FragColor = vec4(col * alpha, alpha);
}
`

const SPARKLE_VERTEX = /* glsl */ `
uniform float uTime;
uniform float uPointSize;
attribute float aPhase;
attribute float aSpeed;
varying float vTwinkle;
void main() {
  vTwinkle = pow(max(0.0, sin(uTime * aSpeed + aPhase)), 8.0);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = uPointSize * (0.6 + 1.4 * vTwinkle);
}
`

const SPARKLE_FRAGMENT = /* glsl */ `
uniform vec3 uColour;
uniform float uBrightness;
varying float vTwinkle;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  if (d > 0.5) discard;
  // Soft core plus a faint four-point glint.
  float core = pow(1.0 - d * 2.0, 3.0);
  float glint = max(0.0, 1.0 - abs(c.x) * 14.0) * max(0.0, 1.0 - abs(c.y) * 2.0)
              + max(0.0, 1.0 - abs(c.y) * 14.0) * max(0.0, 1.0 - abs(c.x) * 2.0);
  float alpha = clamp(core + glint * 0.5, 0.0, 1.0) * vTwinkle * 0.9;
  vec3 col = min(mix(uColour, vec3(1.0), 0.55) * uBrightness, vec3(1.0));
  gl_FragColor = vec4(col * alpha, alpha);
}
`

function fibonacciSphere(n: number): { positions: Float32Array; rand: Float32Array } {
  const positions = new Float32Array(n * 3)
  const rand = new Float32Array(n)
  const golden = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < n; i++) {
    const y = 1 - ((i + 0.5) / n) * 2
    const r = Math.sqrt(1 - y * y)
    const theta = i * golden
    positions.set([Math.cos(theta) * r, y, Math.sin(theta) * r], i * 3)
    rand[i] = Math.random()
  }
  return { positions, rand }
}

/** Latitude rings + meridians as line-segment pairs on the unit sphere. */
function latLongLines(lats: number, longs: number, segments: number): Float32Array {
  const pts: number[] = []
  const push = (phi: number, theta: number) =>
    pts.push(Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta))
  for (let i = 1; i <= lats; i++) {
    const phi = (i / (lats + 1)) * Math.PI
    for (let s = 0; s < segments; s++) {
      push(phi, (s / segments) * Math.PI * 2)
      push(phi, ((s + 1) / segments) * Math.PI * 2)
    }
  }
  for (let i = 0; i < longs; i++) {
    const theta = (i / longs) * Math.PI * 2
    for (let s = 0; s < segments / 2; s++) {
      push((s / (segments / 2)) * Math.PI, theta)
      push(((s + 1) / (segments / 2)) * Math.PI, theta)
    }
  }
  return new Float32Array(pts)
}

function sparkleGeometry(n: number): BufferGeometry {
  const positions = new Float32Array(n * 3)
  const phase = new Float32Array(n)
  const speed = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const u = Math.random() * 2 - 1
    const theta = Math.random() * Math.PI * 2
    const r = 1.25 + Math.random() * 0.5
    const s = Math.sqrt(1 - u * u)
    positions.set([s * Math.cos(theta) * r, u * r, s * Math.sin(theta) * r], i * 3)
    phase[i] = Math.random() * Math.PI * 2
    speed[i] = 0.6 + Math.random() * 1.6
  }
  const g = new BufferGeometry()
  g.setAttribute('position', new BufferAttribute(positions, 3))
  g.setAttribute('aPhase', new BufferAttribute(phase, 1))
  g.setAttribute('aSpeed', new BufferAttribute(speed, 1))
  return g
}

export class OrbScene {
  private readonly renderer: WebGLRenderer
  private readonly scene = new Scene()
  private readonly camera: PerspectiveCamera
  private readonly group = new Group()
  private readonly sparkles: Points
  private readonly sphereMats: ShaderMaterial[]
  private readonly sparkleMat: ShaderMaterial

  private readonly current: Params = { ...BASE }
  private target: Params = { ...BASE }
  private readonly shiftColour = CYAN.clone()
  private targetShiftColour = CYAN
  private state: OrbVisualState = 'idle'
  private hovered = false
  private level = 0.3

  private noiseTime = 0
  private breathPhase = 0
  private pulsePhase = 0
  private elapsed = 0
  private shakeLeft = 0

  private raf = 0
  private running = false
  private lastFrame = 0
  private lastRender = 0

  /** `radiusPx`: on-screen radius of the undisplaced sphere in a `sizePx` square canvas. */
  constructor(canvas: HTMLCanvasElement, sizePx: number, radiusPx: number) {
    this.renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: 'low-power',
    })
    const dpr = window.devicePixelRatio || 1
    this.renderer.setPixelRatio(dpr)
    this.renderer.setSize(sizePx, sizePx, false)
    this.renderer.setClearColor(0x000000, 0)

    const fov = 30
    this.camera = new PerspectiveCamera(fov, 1, 0.1, 50)
    // Half the canvas height in world units is sizePx / 2 / radiusPx at the camera's distance.
    this.camera.position.z = sizePx / 2 / radiusPx / Math.tan((fov * Math.PI) / 360)

    const sizeScale = Math.max(1, Math.min(1.8, radiusPx / 24))
    const sphereUniforms = () => ({
      uNoiseTime: { value: 0 },
      uAmp: { value: BASE.amp },
      uScale: { value: 1 },
      uPointSize: { value: 1.55 * dpr * sizeScale },
      uC0: { value: COLOURS[0] },
      uC1: { value: COLOURS[1] },
      uC2: { value: COLOURS[2] },
      uShiftColour: { value: this.shiftColour },
      uShift: { value: 0 },
      uBrightness: { value: 1 },
      uOpacity: { value: sizeScale > 1.1 ? 0.72 : 0.6 },
    })
    const shared = {
      vertexShader: SPHERE_VERTEX,
      fragmentShader: SPHERE_FRAGMENT,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: AdditiveBlending,
      premultipliedAlpha: true,
    }

    const { positions, rand } = fibonacciSphere(POINT_COUNT)
    const pointsGeom = new BufferGeometry()
    pointsGeom.setAttribute('position', new BufferAttribute(positions, 3))
    pointsGeom.setAttribute('aRand', new BufferAttribute(rand, 1))
    const pointsMat = new ShaderMaterial({
      ...shared,
      uniforms: sphereUniforms(),
      defines: { POINTS: '' },
    })

    const linePositions = latLongLines(8, 12, 48)
    const linesGeom = new BufferGeometry()
    linesGeom.setAttribute('position', new BufferAttribute(linePositions, 3))
    linesGeom.setAttribute(
      'aRand',
      new BufferAttribute(new Float32Array(linePositions.length / 3), 1),
    )
    const linesMat = new ShaderMaterial({ ...shared, uniforms: sphereUniforms() })
    linesMat.uniforms['uOpacity'].value = 0.12

    this.sphereMats = [pointsMat, linesMat]
    this.group.add(new LineSegments(linesGeom, linesMat), new Points(pointsGeom, pointsMat))
    this.group.rotation.x = 0.35

    this.sparkleMat = new ShaderMaterial({
      vertexShader: SPARKLE_VERTEX,
      fragmentShader: SPARKLE_FRAGMENT,
      uniforms: {
        uTime: { value: 0 },
        uPointSize: { value: 4 * dpr },
        uColour: { value: COLOURS[1] },
        uBrightness: { value: 1 },
      },
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: AdditiveBlending,
      premultipliedAlpha: true,
    })
    this.sparkles = new Points(sparkleGeometry(SPARKLE_COUNT), this.sparkleMat)

    this.scene.add(this.group, this.sparkles)
    document.addEventListener('visibilitychange', this.onVisibility)
  }

  setState(state: OrbVisualState): void {
    if (state === this.state) return
    if (state === 'error') this.shakeLeft = SHAKE_S
    this.state = state
    this.target = { ...BASE, ...STATES[state].params }
    this.targetShiftColour = STATES[state].shiftColour
  }

  setHovered(hovered: boolean): void {
    this.hovered = hovered
  }

  /** Mic RMS in [0, 1]; drives displacement while listening (voice lands in P5). */
  setLevel(rms: number): void {
    this.level = Math.min(1, Math.max(0, rms))
  }

  start(): void {
    if (this.running) return
    this.running = true
    if (document.hidden) return
    this.lastFrame = performance.now()
    this.raf = requestAnimationFrame(this.frame)
  }

  stop(): void {
    this.running = false
    cancelAnimationFrame(this.raf)
  }

  dispose(): void {
    this.stop()
    document.removeEventListener('visibilitychange', this.onVisibility)
    this.scene.traverse((obj) => {
      if (obj instanceof Points || obj instanceof LineSegments) {
        obj.geometry.dispose()
        ;(obj.material as ShaderMaterial).dispose()
      }
    })
    this.renderer.dispose()
  }

  private readonly onVisibility = (): void => {
    cancelAnimationFrame(this.raf)
    if (!document.hidden && this.running) {
      this.lastFrame = performance.now()
      this.raf = requestAnimationFrame(this.frame)
    }
  }

  private settled(): boolean {
    const keys = Object.keys(this.target) as (keyof Params)[]
    return keys.every((k) => Math.abs(this.current[k] - this.target[k]) < 1e-3)
  }

  private readonly frame = (now: number): void => {
    if (!this.running) return
    this.raf = requestAnimationFrame(this.frame)

    const active = this.state !== 'idle' || this.hovered || this.shakeLeft > 0 || !this.settled()
    const fps = active ? ACTIVE_FPS : IDLE_FPS
    // Small tolerance so 60 Hz vsync doesn't alternate between 30 and 20 fps.
    if (now - this.lastRender < 1000 / fps - 4) return
    this.lastRender = now

    const dt = Math.min(0.1, (now - this.lastFrame) / 1000)
    this.lastFrame = now
    this.step(dt)
    this.renderer.render(this.scene, this.camera)
  }

  private step(dt: number): void {
    // Exponential approach: ~95 % of the way in TRANSITION_S.
    const k = 1 - Math.exp((-3 * dt) / TRANSITION_S)
    const t = this.target
    const c = this.current
    for (const key of Object.keys(t) as (keyof Params)[]) c[key] += (t[key] - c[key]) * k
    this.shiftColour.lerp(this.targetShiftColour, k)

    const isAudioReactive = this.state === 'listening' || this.state === 'speaking'
    const voiceBoost = isAudioReactive ? this.level : 0
    this.elapsed += dt
    this.noiseTime += dt * (c.noiseSpeed + voiceBoost * 0.95)
    this.breathPhase += dt * Math.PI * 2 * c.breathHz
    this.pulsePhase += dt * Math.PI * 2 * c.pulseHz
    this.group.rotation.y += dt * (c.spin + voiceBoost * 0.45)
    this.sparkles.rotation.y -= dt * c.spin * 0.25

    const pulse = Math.pow(0.5 + 0.5 * Math.sin(this.pulsePhase), 3)
    const scale =
      1 +
      c.breathAmp * Math.sin(this.breathPhase) +
      c.pulseAmp * pulse +
      voiceBoost * 0.14
    const amp = isAudioReactive ? c.amp * (0.35 + 1.85 * this.level) : c.amp
    const brightness = c.brightness * (this.hovered ? 1.15 : 1) + voiceBoost * 0.35

    if (this.shakeLeft > 0) {
      this.shakeLeft = Math.max(0, this.shakeLeft - dt)
      const s = this.shakeLeft / SHAKE_S
      this.group.position.x = Math.sin(this.elapsed * 70) * 0.08 * s
    } else {
      this.group.position.x = 0
    }

    for (const m of this.sphereMats) {
      const u = m.uniforms
      u['uNoiseTime'].value = this.noiseTime
      u['uAmp'].value = amp
      u['uScale'].value = scale
      u['uShift'].value = c.shift
      u['uBrightness'].value = brightness
    }
    this.sparkleMat.uniforms['uTime'].value = this.elapsed
    this.sparkleMat.uniforms['uBrightness'].value = brightness
  }
}
