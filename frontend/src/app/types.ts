export type Activity = 'structure' | 'recipe' | 'materials' | 'results' | 'settings';

export type Selection =
  | { kind: 'none' }
  | { kind: 'step'; id: string }
  | { kind: 'material'; name: string }
  | { kind: 'run'; id: string }
  | { kind: 'artifact'; key: string };

export type DockTab = 'log' | 'flux' | 'intermediate' | 'native' | 'artifacts';
