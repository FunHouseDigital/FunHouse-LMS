/** Identifies the exact app shell executing on this device. */
export const APP_RELEASE_ID =
  typeof __APP_RELEASE_ID__ === 'string' ? __APP_RELEASE_ID__ : 'local';
