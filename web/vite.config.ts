import { defineConfig } from 'vitest/config'

export default defineConfig({
  server: {
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.tsx'],
  },
})
