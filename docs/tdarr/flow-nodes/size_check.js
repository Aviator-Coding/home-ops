// Node: "Guard 1: Size sanity"
// Place: AFTER all ffmpegCommandExecute nodes (they all converge here).
// Output 1 â†’ Pass (continue to duration check)
// Output 2 â†’ Fail (dead-end, source NOT replaced)
// Purpose: Reject encodes that didn't shrink the file meaningfully, OR
//          produced suspiciously small output (likely corrupt).

module.exports = async (args) => {
  const snap = (args.variables && args.variables.user) || {};
  const inputBytes = snap.sourceSize;
  const outputBytes = args.inputFileObj.file_size;

  if (!inputBytes) {
    args.jobLog('Size check: no source snapshot found â€” FAIL (will not replace)');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 2,
      variables: args.variables,
    };
  }

  const ratio = outputBytes / inputBytes;
  args.jobLog(
    `Size check: ${(inputBytes / 1024).toFixed(2)}GiB â†’ ` +
      `${(outputBytes / 1024).toFixed(2)}GiB (${(ratio * 100).toFixed(1)}%)`,
  );

  if (outputBytes < inputBytes * 0.05) {
    args.jobLog('Size check: FAIL â€” output suspiciously small (<5% of source), likely corrupt');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 2,
      variables: args.variables,
    };
  }

  if (ratio >= 0.95) {
    args.jobLog('Size check: FAIL â€” output not meaningfully smaller (>=95% of source)');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 2,
      variables: args.variables,
    };
  }

  return {
    outputFileObj: args.inputFileObj,
    outputNumber: 1,
    variables: args.variables,
  };
};
