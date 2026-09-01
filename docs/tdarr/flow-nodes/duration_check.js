// Node: "Guard 2: Duration integrity"
// Place: AFTER size check.
// Output 1 â†’ Pass
// Output 2 â†’ Fail (truncated or corrupt output)
// Purpose: Catches encodes that crashed mid-way and produced a truncated file.
//          Allows up to 0.5% duration drift (rounding/container differences).

module.exports = async (args) => {
  const snap = (args.variables && args.variables.user) || {};
  const inDur = snap.sourceDuration;

  const fmt = (args.inputFileObj.ffProbeData || {}).format || {};
  const outDur = parseFloat(fmt.duration || 0);

  if (!inDur) {
    args.jobLog('Duration check: no source snapshot â€” PASS (skipping)');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 1,
      variables: args.variables,
    };
  }

  if (!outDur) {
    args.jobLog('Duration check: cannot read output duration â€” FAIL');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 2,
      variables: args.variables,
    };
  }

  const ratio = outDur / inDur;
  args.jobLog(
    `Duration check: ${inDur.toFixed(1)}s â†’ ${outDur.toFixed(1)}s (${(ratio * 100).toFixed(2)}%)`,
  );

  if (ratio < 0.995 || ratio > 1.005) {
    args.jobLog('Duration check: FAIL â€” duration drift > 0.5%');
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
