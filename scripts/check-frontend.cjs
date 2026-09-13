/** Transpile the JSX with the same React preset used by the browser. */
const fs = require('node:fs');
const path = require('node:path');
const babel = require('@babel/standalone');
const root = path.resolve(__dirname, '..');
for (const file of ['components.jsx', 'app.jsx', 'tweaks-panel.jsx']) {
  babel.transform(fs.readFileSync(path.join(root, 'frontend', file), 'utf8'), {presets: ['react']});
  console.log(`JSX OK: ${file}`);
}
