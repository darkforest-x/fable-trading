import type { CapacitorConfig } from '@capacitor/cli';
const config: CapacitorConfig = {
  appId: 'com.spike.discipline',
  appName: 'Spike 纪律交易',
  webDir: 'dist',
  loggingBehavior: 'none',
  server: { androidScheme: 'https' },
  android: { allowMixedContent: false },
};
export default config;
