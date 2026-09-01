// Node: "Is Dolby Vision? (P5 + P7)"
// Place: AFTER guard_home, BEFORE snapshot.
// Output 1 â†’ IS DV (skip, dead-end)
// Output 2 â†’ NOT DV (continue)
// Purpose: Catches single-layer DV (Profile 5) via codec_tag_string AND
//          dual-layer DV (Profile 7, common on UHD discs) via side_data_list.

module.exports = async (args) => {
  const streams = (args.inputFileObj.ffProbeData || {}).streams || [];
  const videoStream = streams.find((s) => s.codec_type === 'video') || {};

  // Profile 5: codec tag tells us
  const tag = (videoStream.codec_tag_string || '').toLowerCase();
  const profile5 = ['dvhe', 'dvh1', 'dvh11', 'dvav', 'dav1'].some((t) => tag.includes(t));

  // Profile 7: signaled via stream side data
  const sideData = videoStream.side_data_list || [];
  const profile7 = sideData.some((sd) =>
    (sd.side_data_type || '').toLowerCase().includes('dovi'),
  );

  const isDV = profile5 || profile7;
  args.jobLog(
    `DV detect: codec_tag="${videoStream.codec_tag_string || ''}" ` +
      `p5=${profile5} p7=${profile7} â†’ ${isDV ? 'IS DV (skip)' : 'NOT DV (continue)'}`,
  );

  return {
    outputFileObj: args.inputFileObj,
    outputNumber: isDV ? 1 : 2,
    variables: args.variables,
  };
};
