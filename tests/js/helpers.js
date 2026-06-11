// Loads browser-JS helper files into a single vm context, mirroring how the
// templates concatenate them into one <script> scope. Top-level `function`
// declarations become context globals — so no module.exports is needed in the
// shipped .js (it stays byte-identical to what the browser runs).
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const JS_DIR = path.join(__dirname, '..', '..', 'src', 'api', 'templates', 'js');

function loadHelpers(...files) {
  const context = {};
  vm.createContext(context);
  for (const file of files) {
    const code = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
    vm.runInContext(code, context, { filename: file });
  }
  return context;
}

module.exports = { loadHelpers };
