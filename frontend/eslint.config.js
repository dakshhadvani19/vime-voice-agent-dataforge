// Flat config: type-aware linting over the app, no rules duplicated from tsc.
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'src/generated/**'] },
  ...tseslint.configs.recommendedTypeChecked.map((c) => ({
    ...c,
    files: ['**/*.{ts,tsx,mts}'],
    languageOptions: {
      ...c.languageOptions,
      globals: { ...globals.browser },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  })),
  {
    rules: {
      // The lab deliberately narrows provider payloads coming off the data channel and SSE;
      // `Record<string, unknown>` at the boundary plus explicit casts at read sites is the design.
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      // React state is updated through one reducer; refs held across renders are intentional.
      'react-hooks/exhaustive-deps': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
)
