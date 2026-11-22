// ScatterplotLayer whose dots move on the GPU. Per dot: origin
// (getPosition), stop point (getTarget) and timing [t0, duration, state,
// phase]. A single `time` uniform drives all motion, so a frame only
// updates one float. States: 0 reached, 1 stranded, 2 care likely exists
// (pulses in place). With `hideBefore`, a dot is invisible until its t0, so a
// second leg of a route appears only when its dot arrives (swarm.ts).

import { ScatterplotLayer } from '@deck.gl/layers';

const swarmModule = {
  name: 'swarm',
  vs: /* glsl */ `\
layout(std140) uniform swarmUniforms {
  float time;
  float hideBefore;
} swarm;
`,
  fs: '',
  uniformTypes: { time: 'f32', hideBefore: 'f32' },
};

function mustReplace(src: string, find: string, repl: string): string {
  if (!src.includes(find)) throw new Error(`SwarmLayer: shader hook not found: ${find.slice(0, 60)}`);
  return src.split(find).join(repl);
}

export interface SwarmProps {
  time: number;
  hideBefore?: boolean;
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
float swarmPulse = instanceTiming.z > 1.5 ? 1.0 + 0.6 * (0.5 + 0.5 * sin(swarm.time * 4.2 + instanceTiming.w)) : 1.0;
`);
    vs = mustReplace(vs, 'project_position_to_clipspace(instancePositions, instancePositions64Low,',
      'project_position_to_clipspace(swarmPos, vec3(0.0),');
    vs = mustReplace(vs, 'float lineWidthPixels = clamp(',
      'outerRadiusPixels *= swarmPulse;\nfloat lineWidthPixels = clamp(');
    vs = mustReplace(vs, 'vFillColor = vec4(instanceFillColors.rgb, instanceFillColors.a * layer.opacity);', `\
vec4 swarmCol = swarmP < 1.0 ? vec4(0.79, 0.82, 0.85, 0.85) : instanceFillColors;
if (instanceTiming.z > 1.5) swarmCol.a *= 0.55 + 0.45 * (0.5 + 0.5 * sin(swarm.time * 4.2 + instanceTiming.w));
if (swarm.hideBefore > 0.5 && swarm.time < instanceTiming.x) swarmCol.a = 0.0;
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (this.state as any).model?.shaderInputs.setProps({ swarm: { time: this.props.time, hideBefore: this.props.hideBefore ? 1 : 0 } });
    super.draw(opts);
  }
}
