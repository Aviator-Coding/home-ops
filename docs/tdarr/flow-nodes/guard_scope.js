// Node: "Guard 0: library scope"
// Place: FIRST, between input1 and guard_home.
// Output 1 -> in scope (continue)
// Output 2 -> OUT OF SCOPE (dead end, no edge: flow stops, nothing is encoded)
// Purpose: Defence in depth behind the library-level processTranscodes gate.
//          librariesToNotProcess turned out to be a Tdarr Pro no-op, so this
//          refuses at execution time regardless of how a job was dispatched.
//          Fails CLOSED: both the library id and the path prefix must match.

module.exports = async (args) => {
  const f = args.inputFileObj || {};
  const filePath = String(f.file || f._id || '');
  const libraryId = String(f.DB || '');

  var ALLOWED_LIBRARY_IDS = ['gEUZf7Nx6']; // "Movies AV1"
  var ALLOWED_PATH_PREFIX = '/media/Movies/';

  const libOk = ALLOWED_LIBRARY_IDS.indexOf(libraryId) !== -1;
  const pathOk = filePath.indexOf(ALLOWED_PATH_PREFIX) === 0;
  const inScope = libOk && pathOk;

  args.jobLog(
    'Scope guard: library="' + libraryId + '" libOk=' + libOk + ' pathOk=' + pathOk +
      ' -> ' + (inScope ? 'IN SCOPE (continue)' : 'OUT OF SCOPE (refused, flow ends here)'),
  );

  return {
    outputFileObj: args.inputFileObj,
    outputNumber: inScope ? 1 : 2,
    variables: args.variables,
  };
};
