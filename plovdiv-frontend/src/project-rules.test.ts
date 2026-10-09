// @vitest-environment node
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

const root = process.cwd()

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walk(path, out)
    else out.push(path)
  }
  return out
}

const sourceFiles = walk(join(root, 'src')).filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith('.test.ts') && !f.endsWith('.test.tsx') && !f.endsWith('.d.ts'))

describe('project rules', () => {
  it('has no inline style attribute in any component', () => {
    const offenders = sourceFiles.filter((f) => f.endsWith('.tsx') && /\bstyle\s*=\s*[{"']/.test(readFileSync(f, 'utf8')))
    expect(offenders).toEqual([])
  })

  it('has an ESLint rule that rejects an inline style attribute', async () => {
    const eslint = new ESLint({ cwd: root })
    const [result] = await eslint.lintText('export const A = () => <div style={{ color: "red" }} />\n', { filePath: join(root, 'src/components/Sample.tsx') })
    expect(result!.messages.map((m) => m.ruleId)).toContain('no-restricted-syntax')
  })

  it('does not bring DuckDB or Parquet readers into the browser bundle', () => {
    const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as { dependencies?: Record<string, string> }
    const deps = Object.keys(manifest.dependencies ?? {})
    expect(deps.filter((d) => /duckdb|parquet|arrow/i.test(d))).toEqual([])
    const offenders = sourceFiles.filter((f) => /duckdb|hyparquet|parquet-wasm/i.test(readFileSync(f, 'utf8')))
    expect(offenders).toEqual([])
  })

  it('formats times only through Intl, with no date library', () => {
    const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as { dependencies?: Record<string, string> }
    expect(Object.keys(manifest.dependencies ?? {}).filter((d) => /moment|dayjs|date-fns|luxon/.test(d))).toEqual([])
    const offenders = sourceFiles.filter((f) => /new Date\(\s*\w+\s*,\s*\w+\s*,/.test(readFileSync(f, 'utf8')))
    expect(offenders).toEqual([])
  })

  it('keys data queries on the run id', () => {
    const hooks = readFileSync(join(root, 'src/queries/hooks.ts'), 'utf8')
    for (const key of ['series', 'daily', 'trend', 'risk-latest', 'model-card', 'model-calibration']) {
      expect(hooks).toMatch(new RegExp(`queryKey: \\['${key}', run`))
    }
  })
})
