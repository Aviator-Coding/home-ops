// Node: "Snapshot source metadata"
// Place: AFTER guard_home + dv_check, BEFORE is_hevc / is_av1.
// Purpose: Captures source file size, duration, and HDR status into args.variables
//          so post-encode guards can compare against original (args.inputFileObj
//          gets mutated by ffmpegCommandExecute to point at the encoded output).

module.exports = async (args) => {
  const streams = (args.inputFileObj.ffProbeData || {}).streams || [];
  const videoStream = streams.find((s) => s.codec_type === 'video') || {};
  const fmt = (args.inputFileObj.ffProbeData || {}).format || {};

  args.variables = args.variables || {};
  args.variables.user = args.variables.user || {};

  args.variables.user.sourceSize = args.inputFileObj.file_size;
  args.variables.user.sourceDuration = parseFloat(fmt.duration || 0);
  args.variables.user.sourceWasHDR = (videoStream.color_transfer || '') === 'smpte2084';

  args.jobLog(
    `Snapshot: size=${(args.variables.user.sourceSize / 1024).toFixed(2)}GiB ` +
      `duration=${args.variables.user.sourceDuration.toFixed(1)}s ` +
      `HDR=${args.variables.user.sourceWasHDR}`,
  );

  return {
    outputFileObj: args.inputFileObj,
    outputNumber: 1,
    variables: args.variables,
  };
};
