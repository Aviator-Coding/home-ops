# Recyclarr scores by trash_id

`assign_scores_to` entries in
`kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml` must name a
`trash_id`. A match on profile `name` breaks when TRaSH renames the profile:
scores stay on whatever profile still has the old name, and Recyclarr creates
a new profile under the new name with none of the custom-format scores. That
is how `[SQP] SQP-1 (2160p)` was scored onto a 2-movie orphan while the
240-movie profile in use kept the old name (PR #1369 landed on the orphan).

Which movie uses which quality profile is Radarr database state. Recyclarr
creates and scores profiles. It does not assign a movie to one. A profile
restructure needs a one-off Radarr API pass after the config sync:

1. `GET /api/v3/qualityprofile` and confirm the `trash_id` you scored is the
   profile movies actually reference.
2. Move movies with Radarr's movie editor or `PUT /api/v3/movie/editor`.
   Git will not do this.
3. Trigger a Recyclarr sync and confirm custom-format scores on the live
   profile, not on a same-named empty one.

`min_format_score` on a profile is a hard reject. An override that sits above
every release's score makes the profile accept nothing, which looks like
"indexers are down" (`Reports grabbed: 0`). Read the score on a rejected
release before changing indexers.

Manual sync:
`kubectl -n downloads create job --from=cronjob/recyclarr recyclarr-manual-$(date +%s)`.

Import lists are not in the Recyclarr file. `searchOnAdd` is Radarr
Settings, Lists. See [backlog-search.md](backlog-search.md).

`HD Bluray + WEB` (`d1d67249...`) and `UHD Bluray + WEB` (`64fb5f9a...`) are
declared for the catalogue titles no TRaSH tier group ever released. Their
guide `minFormatScore` is 0 and language Original, so the unwanted formats
(LQ, BR-DISK, x265 (HD), AV1) are assigned to them explicitly. SQP-1 keeps
its minimum of 1000; never lower it to rescue old titles, move them instead.
