import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

const alias = { '@': path.resolve(__dirname, './src') }

export default defineConfig({
  plugins: [react()],
  resolve: { alias },
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: 'stores',
          environment: 'node',
          include: [
            'src/**/*.test.ts',
            'tests/unit/stores/**/*.test.ts',
          ],
          exclude: ['tests/e2e/**'],
        },
      },
      {
        extends: true,
        test: {
          name: 'api',
          environment: 'node',
          include: ['tests/unit/lib/**/*.test.ts'],
          setupFiles: ['./tests/setup/vitest.setup.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'components',
          environment: 'jsdom',
          include: [
            'tests/unit/components/**/*.test.tsx',
            'tests/unit/hooks/**/*.test.ts',
          ],
          setupFiles: ['./tests/setup/vitest.setup.ts'],
        },
      },
    ],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/types/**',
        'src/components/ui/**',
      ],
    },
  },
})
