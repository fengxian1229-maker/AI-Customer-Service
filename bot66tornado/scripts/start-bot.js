'use strict';

const { main } = require('../src/runtime/poller');

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
