// ScatterplotLayer whose dots move on the GPU. Per dot: leg start
// (getPosition), leg end (getTarget) and timing [t0, duration, state, phase].
// A single `time` uniform drives all motion, so a frame only updates one
// float; the arrays change only when a dot's leg changes.
//
// Two modes:
// - agentMode (the agent simulator, app/src/sim): the colour is always
//   instanceFillColors. A dot is invisible before its t0 (not yet departed).
//   state 2 pulses in place (holding, or stuck) on the `pulse` clock (real
//   seconds, so the pulse rate does not depend on the playback speed);
//   state 3 is arrived: alpha fades to 0 over 3 sim-minutes after t0 + dur.
// - legacy (the retired RouteSwarm trip view in minimap.ts, until B's map
//   replaces it): grey while moving, the fill colour once stopped, state 2
//   pulses, and with `hideBefore` a dot is invisible until its t0.

import { ScatterplotLayer } from '@deck.gl/layers';

const swarmModule = {
  name: 'swarm',
  vs: /* glsl */ `\
layout(std140) uniform swarmUniforms {
  float time;
  float hideBefore;
  float agentMode;
  float pulse;
} swarm;
`,
  fs: '',
  uniformTypes: { time: 'f32', hideBefore: 'f32', agentMode: 'f32', pulse: 'f32' },
};

function mustReplace(src: string, find: string, repl: string): string {
  if (!src.includes(find)) throw new Error(`SwarmLayer: shader hook not found: ${find.slice(0, 60)}`);
  return src.split(find).join(repl);
}

export interface SwarmProps {
  /** sim time (agent mode: sim-minutes) */
  time: number;
  hideBefore?: boolean;
  /** agent simulator rendering (see the header) */
  agentMode?: boolean;
  /** real-time clock for pulses, seconds (agent mode) */
  pulse?: number;
  getTarget: unknown;
  getTiming: unknown;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class SwarmLayer extends ScatterplotLayer<any, SwarmProps> {
  static layerName = 'SwarmLayer';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  static defaultProps: any = {
    ...ScatterplotLayer.defaultProps,
    time: 0,
    hideBefore: false,
    agentMode: false,
    pulse: 0,
    getTarget: { type: 'accessor', value: [0, 0, 0] },
    getTiming: { type: 'accessor', value: [0, 0, 0, 0] },
  };

  getShaders() {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const s: any = super.getShaders();
    let vs: string = s.vs;
    vs = mustReplace(vs, 'in vec3 instancePositions64Low;',
      'in vec3 instancePositions64Low;\nin vec3 instanceTargets;\nin vec4 instanceTiming;');
    vs = mustReplace(vs, 'void main(void) {', `void main(void) {
float swarmP = clamp((swarm.time - instanceTiming.x) / max(instanceTiming.y, 0.0001), 0.0, 1.0);
vec3 swarmPos = mix(instancePositions, instanceTargets, swarmP);
float swarmWave = swarm.agentMode > 0.5
  ? 0.5 + 0.5 * sin(swarm.pulse * 5.0 + instanceTiming.w)
  : 0.5 + 0.5 * sin(swarm.time * 4.2 + instanceTiming.w);
float swarmPulse = (instanceTiming.z > 1.5 && instanceTiming.z < 2.5) || (swarm.agentMode < 0.5 && instanceTiming.z > 1.5)
  ? 1.0 + 0.6 * swarmWave : 1.0;
`);
    vs = mustReplace(vs, 'project_position_to_clipspace(instancePositions, instancePositions64Low,',
      'project_position_to_clipspace(swarmPos, vec3(0.0),');
    vs = mustReplace(vs, 'float lineWidthPixels = clamp(',
      'outerRadiusPixels *= swarmPulse;\nfloat lineWidthPixels = clamp(');
    vs = mustReplace(vs, 'vFillColor = vec4(instanceFillColors.rgb, instanceFillColors.a * layer.opacity);', `\
vec4 swarmCol;
if (swarm.agentMode > 0.5) {
  swarmCol = instanceFillColors;
  if (swarm.time < instanceTiming.x) swarmCol.a = 0.0;
  if (instanceTiming.z > 1.5 && instanceTiming.z < 2.5) swarmCol.a *= 0.55 + 0.45 * swarmWave;
  if (instanceTiming.z > 2.5) swarmCol.a *= 1.0 - clamp((swarm.time - instanceTiming.x - instanceTiming.y) / 3.0, 0.0, 1.0);
} else {
  swarmCol = swarmP < 1.0 ? vec4(0.79, 0.82, 0.85, 0.85) : instanceFillColors;
  if (instanceTiming.z > 1.5) swarmCol.a *= 0.55 + 0.45 * swarmWave;
  if (swarm.hideBefore > 0.5 && swarm.time < instanceTiming.x) swarmCol.a = 0.0;
}
vFillColor = vec4(swarmCol.rgb, swarmCol.a * layer.opacity);`);
    s.vs = vs;
    s.modules = [...s.modules, swarmModule];
    return s;
  }

  initializeState() {
    super.initializeState();
    this.getAttributeManager()!.addInstanced({
      instanceTargets: { size: 3, accessor: 'getTarget', defaultValue: [0, 0, 0] },
      instanceTiming: { size: 4, accessor: 'getTiming', defaultValue: [0, 0, 0, 0] },
    });
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  draw(opts: any) {
    const p = this.props;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (this.state as any).model?.shaderInputs.setProps({
      swarm: { time: p.time, hideBefore: p.hideBefore ? 1 : 0, agentMode: p.agentMode ? 1 : 0, pulse: p.pulse ?? 0 },
    });
    super.draw(opts);
  }
}
