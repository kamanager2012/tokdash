import type { TokDashBridge } from './electron-api';

declare global {
  interface Window {
    tokdash?: TokDashBridge;
  }
}

export {};
