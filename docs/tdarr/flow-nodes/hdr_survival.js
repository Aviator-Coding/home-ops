// Node: "Guard 3: HDR metadata survived"
// Place: AFTER duration check, BEFORE rename.
// Output 1 â†’ Pass (continue to rename â†’ replace)
// Output 2 â†’ Fail (HDR side data was lost in encode, source NOT replaced)
// Purpose: Ensures mastering display + content light level survived the QSV pipeline.
//          SDR sources pass through automatically (no HDR to check).

module.exports = async (args) => {
  const snap = (args.variables && args.variables.user) || {};

  if (!snap.sourceWasHDR) {
    args.jobLog('HDR survival: source was SDR â€” PASS (no HDR check needed)');
    return {
      outputFileObj: args.inputFileObj,
      outputNumber: 1,
      variables: args.variables,
    };
  }

  // First check stream-level side data (fast, already in ffProbeData)
  const streams = (args.inputFileObj.ffProbeData || {}).streams || [];
  const videoStream = streams.find((s) => s.codec_type === 'video') || {};
  const streamSd = videoStream.side_data_list || [];

  let hasMaster = streamSd.some((x) =>
    (x.side_data_type || '').toLowerCase().includes('mastering'),
  );
  let hasCLL = streamSd.some((x) =>
    (x.side_data_type || '').toLowerCase().includes('content light'),
  );

  // Fall back to frame-level probe if stream-level didn't have it
  if (!hasMaster || !hasCLL) {
    try {
      const { execSync } = require('child_process');
      const out = execSync(
        `ffprobe -v error -select_streams v:0 -read_intervals "%+#1" ` +
          `-show_frames -show_entries frame=side_data_list -of json ` +
          `"${args.inputFileObj._id}"`,
        { maxBuffer: 10 * 1024 * 1024 },
      ).toString();
      const frameSd = ((JSON.parse(out).frames || [])[0] || {}).side_data_list || [];
      hasMaster =
        hasMaster ||
        frameSd.some((x) => (x.side_data_type || '').toLowerCase().includes('mastering'));
      hasCLL =
        hasCLL ||
        frameSd.some((x) => (x.side_data_type || '').toLowerCase().includes('content light'));
    } catch (e) {
      args.jobLog(`HDR survival: frame-level ffprobe failed (${e.message})`);
    }
  }

  args.jobLog(`HDR survival: mastering=${hasMaster} cll=${hasCLL}`);

  if (!hasMaster || !hasCLL) {
    args.jobLog('HDR survival: FAIL â€” HDR side data missing from output');
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
