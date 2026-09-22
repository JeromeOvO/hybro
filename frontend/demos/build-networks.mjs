import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'
import postcss from 'postcss'
import tailwindcss from '@tailwindcss/postcss'

// Run from the repository root: node frontend/demos/build-networks.mjs
// All paths are based on this file, so an absolute invocation works from any cwd.
const demoDirectory = dirname(fileURLToPath(import.meta.url))
const frontendDirectory = resolve(demoDirectory, '..')
const outputDirectory = join(demoDirectory, 'dist')
const cssSource = join(demoDirectory, 'networks.css')
const cssOutput = join(outputDirectory, 'networks.css')

const [javascript, stylesheet] = await Promise.all([
  build({
    absWorkingDir: frontendDirectory,
    entryPoints: [join(demoDirectory, 'networks.tsx')],
    outfile: join(outputDirectory, 'networks.js'),
    bundle: true,
    write: false,
    minify: true,
    platform: 'browser',
    format: 'iife',
    target: ['es2022'],
    jsx: 'automatic',
    alias: { '@': join(frontendDirectory, 'src') },
    tsconfig: join(frontendDirectory, 'tsconfig.json'),
    define: { 'process.env.NODE_ENV': '"production"' },
    legalComments: 'eof',
  }),
  readFile(cssSource, 'utf8').then((css) =>
    postcss([tailwindcss({ base: demoDirectory, optimize: { minify: true } })])
      .process(css, { from: cssSource, to: cssOutput, map: false })
  ),
])

for (const warning of stylesheet.warnings()) {
  console.warn(warning.toString())
}

await mkdir(outputDirectory, { recursive: true })
await Promise.all([
  ...javascript.outputFiles.map((file) => writeFile(file.path, file.contents)),
  writeFile(cssOutput, stylesheet.css),
])

console.log('Built frontend/demos/dist/networks.js and networks.css')
console.log('Open frontend/demos/networks.html directly or serve frontend/demos locally.')
