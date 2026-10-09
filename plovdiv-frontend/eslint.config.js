import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import solid from 'eslint-plugin-solid/configs/typescript'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'contracts', 'public', 'src/api/schema.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    ...solid,
    languageOptions: {
      ...solid.languageOptions,
      globals: { ...globals.browser },
    },
    rules: {
      ...solid.rules,
      // Styling goes through Tailwind utilities and CSS variables, never inline styles.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='style']",
          message: 'Inline styles are not allowed. Use Tailwind utilities or a CSS variable.',
        },
      ],
    },
  },
  {
    files: ['src/**/*.test.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
  {
    files: ['*.config.{js,ts}', 'vite.config.ts'],
    languageOptions: { globals: { ...globals.node } },
  },
)
