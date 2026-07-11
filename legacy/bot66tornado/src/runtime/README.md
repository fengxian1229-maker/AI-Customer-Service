# Missing legacy runtime source

This prototype imports JavaScript modules from this directory, including
`case-store.js`, `poller.js`, `process-lock.js`, and `process-scan.js`.

The directory was historically excluded by an overly broad `runtime/` ignore
rule and its source files were never committed. Restore the original files from
the operator backup before running the Node.js prototype. The ignore rule now
targets only the repository-root `/runtime/` state directory so restored source
files can be versioned normally.
